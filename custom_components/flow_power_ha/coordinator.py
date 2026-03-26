"""Data update coordinator for Flow Power HA."""
from __future__ import annotations

import logging
import time as time_mod
from datetime import datetime, timedelta
from typing import Any

import aiohttp
try:
    from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue, async_delete_issue
except ImportError:
    try:
        from homeassistant.components.repairs import IssueSeverity, async_create_issue, async_delete_issue
    except ImportError:
        # HA version too old for repairs — stub out
        IssueSeverity = None  # type: ignore[assignment,misc]

        def async_create_issue(*args, **kwargs):  # type: ignore[misc]
            pass

        def async_delete_issue(*args, **kwargs):  # type: ignore[misc]
            pass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_clients import AEMOClient, AmberClient, FlowPowerPortalClient
from .const import (
    CONF_AMBER_API_KEY,
    CONF_AMBER_SITE_ID,
    CONF_BASE_RATE,
    CONF_FLOWPOWER_EMAIL,
    CONF_FLOWPOWER_PASSWORD,
    CONF_NEM_REGION,
    CONF_PEA_CUSTOM_VALUE,
    CONF_PEA_ENABLED,
    CONF_PRICE_SOURCE,
    DEFAULT_BASE_RATE,
    DEFAULT_TWAP_WINDOW_DAYS,
    DOMAIN,
    MIN_TWAP_SAMPLES,
    PRICE_SOURCE_AEMO,
    PRICE_SOURCE_AMBER,
    PRICE_SOURCE_FLOWPOWER,
    UPDATE_INTERVAL_CURRENT,
    UPDATE_INTERVAL_FLOWPOWER,
)
from .pricing import (
    calculate_export_price,
    calculate_forecast_prices,
    calculate_import_price,
)

_LOGGER = logging.getLogger(__name__)

# AEMO polling - every 30 seconds for more responsive updates
AEMO_POLL_SECONDS = [0, 30]  # Poll at :00 and :30 of every minute


class FlowPowerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for fetching Flow Power price data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
    ) -> None:
        """Initialize the coordinator."""
        # Use longer fallback interval - primary updates are clock-aligned
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_CURRENT),
        )

        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._aemo_client: AEMOClient | None = None
        self._amber_client: AmberClient | None = None
        self._fp_client: FlowPowerPortalClient | None = None
        self._unsub_time_listeners: list = []

        # Configuration
        self.price_source = config.get(CONF_PRICE_SOURCE, PRICE_SOURCE_AEMO)
        self.region = config.get(CONF_NEM_REGION, "NSW1")
        self.base_rate = config.get(CONF_BASE_RATE, DEFAULT_BASE_RATE)
        self.pea_enabled = config.get(CONF_PEA_ENABLED, True)
        self.pea_custom_value = config.get(CONF_PEA_CUSTOM_VALUE)

        # Amber config
        self.amber_api_key = config.get(CONF_AMBER_API_KEY)
        self.amber_site_id = config.get(CONF_AMBER_SITE_ID)

        # Flow Power portal config (may come from initial data or options)
        self.fp_email = config.get(CONF_FLOWPOWER_EMAIL)
        self.fp_password = config.get(CONF_FLOWPOWER_PASSWORD)
        self.fp_enabled = bool(self.fp_email and self.fp_password)

        # TWAP tracking
        self._price_history: list[dict[str, Any]] = []
        self._store = Store(hass, 1, f"{DOMAIN}.price_history.{self.region}")
        self._last_store_save: int | None = None
        self._twap: float | None = None

        # Flow Power portal data
        self._fp_data: dict[str, Any] | None = None
        self._fp_last_fetch: float = 0
        self._fp_auth_failed: bool = False
        self._fp_restore_failures: int = 0
        self._fp_restore_backoff_until: float = 0

        # Persistent cookie storage for Flow Power portal session
        self._fp_cookie_store = Store(hass, 1, f"{DOMAIN}.fp_session")

        # Persistent portal data cache (survives restarts)
        self._fp_data_store = Store(hass, 1, f"{DOMAIN}.fp_portal_data")

        # Set up clock-aligned polling
        self._setup_time_listeners()

    def _setup_time_listeners(self) -> None:
        """Set up clock-aligned time listeners for price updates."""
        # AEMO data updates: Poll every 30 seconds
        unsub_aemo = async_track_time_change(
            self.hass,
            self._handle_aemo_update,
            second=AEMO_POLL_SECONDS,
        )
        self._unsub_time_listeners.append(unsub_aemo)

        # Happy Hour transitions: Update exactly at 17:30:00 and 19:30:00
        unsub_happy_hour = async_track_time_change(
            self.hass,
            self._handle_happy_hour_update,
            hour=[17, 19],
            minute=[30],
            second=[0],
        )
        self._unsub_time_listeners.append(unsub_happy_hour)

        _LOGGER.info(
            "Flow Power: Polling enabled - AEMO every 30 seconds, "
            "Happy Hour at 17:30:00/19:30:00"
        )

    @callback
    def _handle_aemo_update(self, now: datetime) -> None:
        """Handle clock-aligned AEMO update."""
        _LOGGER.debug("Flow Power: Clock-aligned AEMO update triggered at %s", now)
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _handle_happy_hour_update(self, now: datetime) -> None:
        """Handle Happy Hour transition update."""
        _LOGGER.info("Flow Power: Happy Hour transition update at %s", now)
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_setup(self) -> None:
        """Set up the coordinator."""
        self._session = aiohttp.ClientSession()

        if self.price_source == PRICE_SOURCE_AEMO:
            self._aemo_client = AEMOClient(self._session)
        elif self.price_source == PRICE_SOURCE_AMBER and self.amber_api_key:
            self._amber_client = AmberClient(
                self._session,
                self.amber_api_key,
                self.amber_site_id,
            )
        elif self.price_source == PRICE_SOURCE_FLOWPOWER:
            # Flow Power portal uses AEMO for spot prices + portal for account data
            self._aemo_client = AEMOClient(self._session)

        # Pick up authenticated portal client from config/options flow if available
        pending = self.hass.data.get(DOMAIN, {}).pop("_pending_fp_client", None)
        _LOGGER.debug(
            "Flow Power: Setup - price_source=%s, fp_enabled=%s, pending_client=%s",
            self.price_source, self.fp_enabled,
            f"authenticated={pending.is_authenticated}" if pending else "None",
        )
        if pending and pending.is_authenticated:
            self._fp_client = pending
            _LOGGER.info("Flow Power: Using authenticated portal client from config flow")
            # Persist session cookies for surviving restarts
            await self._save_fp_cookies()
        elif self.fp_enabled:
            # Try to restore session from stored cookies
            self._fp_client = FlowPowerPortalClient()
            await self._fp_authenticate()

        # Restore cached portal data so sensors don't go unknown on restart
        if self.fp_enabled and not self._fp_data:
            cached = await self._fp_data_store.async_load()
            if cached and isinstance(cached.get("data"), dict):
                self._fp_data = cached["data"]
                self._fp_data["cached"] = True
                _LOGGER.info(
                    "Flow Power: Restored cached portal data "
                    "(PEA=%.2f, TWAP=%.2f) — will refresh when session restored",
                    self._fp_data.get("pea_actual", 0),
                    self._fp_data.get("twap", 0),
                )

        # Load stored price history for TWAP calculation
        stored = await self._store.async_load()
        if stored and isinstance(stored.get("price_history"), list):
            self._price_history = stored["price_history"]
            self._prune_history()
            self._twap = self._calculate_twap()
            _LOGGER.info(
                "Loaded %d price history entries, TWAP: %s c/kWh (%.1f days)",
                len(self._price_history),
                self._twap,
                self._get_twap_days(),
            )

    async def _fp_authenticate(self) -> None:
        """Restore Flow Power portal session from stored cookies."""
        if not self._fp_client:
            return

        stored = await self._fp_cookie_store.async_load()
        if stored and stored.get("cookies"):
            _LOGGER.info(
                "Flow Power: Found %d stored session cookies, attempting restore",
                len(stored["cookies"]),
            )
            self._fp_client.import_session_cookies(stored["cookies"])

            try:
                success = await self._fp_client.restore_session()
                if success:
                    _LOGGER.info("Flow Power: Session restored from stored cookies")
                    return
                else:
                    _LOGGER.warning(
                        "Flow Power: Stored session expired — "
                        "re-authenticate via Options > Re-authenticate Flow Power"
                    )
                    self._fp_auth_failed = True
            except Exception as e:
                _LOGGER.error("Flow Power: Session restore error: %s", e)
                self._fp_auth_failed = True
        else:
            _LOGGER.info(
                "Flow Power: No stored session — "
                "authenticate via Options > Re-authenticate Flow Power"
            )

    async def _save_fp_cookies(self) -> None:
        """Persist the Flow Power session cookies to HA storage."""
        if not self._fp_client:
            return

        try:
            cookies = self._fp_client.export_session_cookies()
            if cookies:
                await self._fp_cookie_store.async_save({"cookies": cookies})
                _LOGGER.debug(
                    "Flow Power: Saved %d session cookies to persistent storage",
                    len(cookies),
                )
        except Exception as e:
            _LOGGER.error("Flow Power: Error saving session cookies: %s", e)

    async def _save_fp_data_cache(self) -> None:
        """Persist the last known portal data so sensors survive restarts."""
        if not self._fp_data:
            return
        try:
            # Strip the cached flag before saving
            save_data = {k: v for k, v in self._fp_data.items() if k != "cached"}
            await self._fp_data_store.async_save({"data": save_data})
        except Exception as e:
            _LOGGER.error("Flow Power: Error saving portal data cache: %s", e)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        if self._session is None:
            await self._async_setup()

        try:
            data: dict[str, Any] = {
                "import_price": None,
                "export_price": None,
                "wholesale_price": None,
                "forecast": [],
                "last_update": None,
                "twap": self._twap,
                "twap_days": self._get_twap_days(),
                "twap_samples": len(self._price_history),
                "flowpower_data": None,
            }

            # Fetch Flow Power portal account data (or serve cached data)
            if self.fp_enabled:
                await self._fetch_flowpower_data(data)

            # Fetch current prices based on source
            if self.price_source in (PRICE_SOURCE_AEMO, PRICE_SOURCE_FLOWPOWER) and self._aemo_client:
                current_prices = await self._aemo_client.get_current_prices()
                region_data = current_prices.get(self.region, {})

                if region_data:
                    wholesale_cents = region_data.get("price_cents", 0)

                    # Record wholesale price for TWAP calculation
                    self._record_price(wholesale_cents)
                    data["twap"] = self._twap
                    data["twap_days"] = self._get_twap_days()
                    data["twap_samples"] = len(self._price_history)

                    # Use Flow Power portal TWAP if available (more accurate)
                    twap_for_calc = self._twap
                    if self._fp_data and self._fp_data.get("twap") is not None:
                        twap_for_calc = self._fp_data["twap"]

                    # Calculate import price with dynamic TWAP
                    import_info = calculate_import_price(
                        wholesale_cents=wholesale_cents,
                        base_rate=self.base_rate,
                        pea_enabled=self.pea_enabled,
                        pea_custom_value=self.pea_custom_value,
                        twap=twap_for_calc,
                    )

                    data["import_price"] = import_info
                    data["wholesale_price"] = wholesale_cents
                    data["last_update"] = region_data.get("timestamp")

                # Fetch forecast
                forecast_raw = await self._aemo_client.get_price_forecast(
                    self.region, periods=96
                )
                _LOGGER.info("AEMO forecast raw periods: %d for %s", len(forecast_raw) if forecast_raw else 0, self.region)

                # Use portal TWAP for forecast calculations too
                twap_for_forecast = self._twap
                if self._fp_data and self._fp_data.get("twap") is not None:
                    twap_for_forecast = self._fp_data["twap"]

                if forecast_raw:
                    data["forecast"] = calculate_forecast_prices(
                        forecast_raw,
                        base_rate=self.base_rate,
                        pea_enabled=self.pea_enabled,
                        pea_custom_value=self.pea_custom_value,
                        twap=twap_for_forecast,
                    )
                    _LOGGER.info("Calculated forecast periods: %d", len(data["forecast"]))

            elif self.price_source == PRICE_SOURCE_AMBER and self._amber_client:
                current_prices = await self._amber_client.get_current_prices()

                # Find general (import) channel
                for price in current_prices:
                    if price.get("channelType") == "general":
                        wholesale_cents = self._amber_client.extract_wholesale_price(price)

                        # Record wholesale price for TWAP calculation
                        self._record_price(wholesale_cents)
                        data["twap"] = self._twap
                        data["twap_days"] = self._get_twap_days()
                        data["twap_samples"] = len(self._price_history)

                        # Calculate import price with dynamic TWAP
                        import_info = calculate_import_price(
                            wholesale_cents=wholesale_cents,
                            base_rate=self.base_rate,
                            pea_enabled=self.pea_enabled,
                            pea_custom_value=self.pea_custom_value,
                            twap=self._twap,
                        )

                        data["import_price"] = import_info
                        data["wholesale_price"] = wholesale_cents
                        data["last_update"] = price.get("nemTime")
                        break

                # Fetch forecast
                forecast_raw = await self._amber_client.get_price_forecast(
                    next_hours=48, resolution=30
                )
                # Filter to general channel
                general_forecast = [
                    p for p in forecast_raw
                    if p.get("channelType") == "general"
                ]
                if general_forecast:
                    data["forecast"] = calculate_forecast_prices(
                        general_forecast,
                        base_rate=self.base_rate,
                        pea_enabled=self.pea_enabled,
                        pea_custom_value=self.pea_custom_value,
                        twap=self._twap,
                    )

            # Calculate export price (always based on region and time)
            data["export_price"] = calculate_export_price(self.region)

            return data

        except Exception as err:
            _LOGGER.error("Error fetching Flow Power data: %s", err)
            raise UpdateFailed(f"Error fetching data: {err}") from err

    async def _check_pending_fp_client(self) -> bool:
        """Check for a freshly authenticated client from reauth flow.

        Returns True if a new client was picked up.
        """
        pending = self.hass.data.get(DOMAIN, {}).pop("_pending_fp_client", None)
        if pending and pending.is_authenticated:
            # Close the old client's session to avoid "Unclosed client session"
            if self._fp_client:
                await self._fp_client.close()
            self._fp_client = pending
            self._fp_auth_failed = False
            self._fp_restore_failures = 0
            self._fp_restore_backoff_until = 0
            # Clear the reauth repair alert
            async_delete_issue(self.hass, DOMAIN, "session_expired")
            _LOGGER.info("Flow Power: Picked up authenticated client from reauth flow")
            return True
        return False

    async def _fetch_flowpower_data(self, data: dict[str, Any]) -> None:
        """Fetch account data from the Flow Power portal.

        Updates self._fp_data and data["flowpower_data"] on success.
        Only fetches every UPDATE_INTERVAL_FLOWPOWER seconds.
        """
        # Always check for a freshly authenticated client from reauth
        if await self._check_pending_fp_client():
            await self._save_fp_cookies()

        if not self._fp_client:
            # Still serve cached data even without a client
            if self._fp_data:
                data["flowpower_data"] = self._fp_data
            return

        now = time_mod.time()
        if now - self._fp_last_fetch < UPDATE_INTERVAL_FLOWPOWER and self._fp_data:
            # Use cached data
            data["flowpower_data"] = self._fp_data
            return

        account_data = await self._fp_client.get_account_data()
        if account_data:
            account_data.pop("cached", None)  # Clear stale flag
            self._fp_data = account_data
            self._fp_last_fetch = now
            data["flowpower_data"] = account_data
            self._fp_restore_failures = 0
            self._fp_restore_backoff_until = 0
            async_delete_issue(self.hass, DOMAIN, "session_expired")
            _LOGGER.info(
                "Flow Power: Account data updated - TWAP=%.2f, PEA=%.2f, LWAP=%.2f",
                account_data.get("twap", 0),
                account_data.get("pea_actual", 0),
                account_data.get("lwap", 0),
            )
            # Keep stored cookies fresh after each successful fetch
            await self._save_fp_cookies()
            await self._save_fp_data_cache()
        elif not self._fp_client.is_authenticated:
            # Session expired — try restoring (with backoff)
            if now < self._fp_restore_backoff_until:
                # In backoff period — use cached data or stay silent
                if self._fp_data:
                    data["flowpower_data"] = self._fp_data
                return

            _LOGGER.info("Flow Power: Session expired, attempting restore")
            if await self._fp_client.restore_session():
                self._fp_restore_failures = 0
                self._fp_restore_backoff_until = 0
                async_delete_issue(self.hass, DOMAIN, "session_expired")
                await self._save_fp_cookies()
                # Retry the fetch with restored session
                account_data = await self._fp_client.get_account_data()
                if account_data:
                    account_data.pop("cached", None)
                    self._fp_data = account_data
                    self._fp_last_fetch = now
                    data["flowpower_data"] = account_data
                    _LOGGER.info("Flow Power: Account data fetched after session restore")
                    await self._save_fp_data_cache()
                    return
            # Restore failed — apply exponential backoff (30s, 60s, 120s, ... max 10min)
            self._fp_restore_failures += 1
            backoff = min(30 * (2 ** (self._fp_restore_failures - 1)), 600)
            self._fp_restore_backoff_until = now + backoff
            # Raise repair alert once backoff reaches max (600s)
            if backoff >= 600 and IssueSeverity is not None:
                async_create_issue(
                    self.hass,
                    DOMAIN,
                    "session_expired",
                    is_fixable=False,
                    severity=IssueSeverity.WARNING,
                    translation_key="session_expired",
                )
            if self._fp_data:
                data["flowpower_data"] = self._fp_data
                _LOGGER.warning(
                    "Flow Power: Using cached portal data (restore failed, "
                    "retry in %ds — re-authenticate via Options if persistent)",
                    backoff,
                )
            else:
                _LOGGER.warning(
                    "Flow Power: No portal data available (session expired, "
                    "retry in %ds — re-authenticate via Options)",
                    backoff,
                )
        elif self._fp_data:
            # Use stale cached data
            data["flowpower_data"] = self._fp_data
            _LOGGER.warning("Flow Power: Using cached portal data (fetch failed)")
        else:
            _LOGGER.warning("Flow Power: No portal data available")

    def _record_price(self, wholesale_cents: float) -> None:
        """Record a wholesale price sample for TWAP calculation."""
        now = int(time_mod.time())

        # Deduplicate: don't store more than once per 4 minutes
        if self._price_history:
            last_ts = self._price_history[-1]["ts"]
            if now - last_ts < 240:
                return

        self._price_history.append({
            "ts": now,
            "price": round(wholesale_cents, 2),
        })

        self._prune_history()
        self._twap = self._calculate_twap()

        # Save periodically (every 10 minutes)
        if self._last_store_save is None or now - self._last_store_save > 600:
            self.hass.async_create_task(self._async_save_history())
            self._last_store_save = now

    def _prune_history(self) -> None:
        """Remove price history entries older than the TWAP window."""
        cutoff = int(time_mod.time()) - (DEFAULT_TWAP_WINDOW_DAYS * 86400)
        self._price_history = [
            p for p in self._price_history if p["ts"] >= cutoff
        ]

    def _calculate_twap(self) -> float | None:
        """Calculate the Time Weighted Average Price from history.

        Returns TWAP in c/kWh, or None if insufficient data.
        """
        if len(self._price_history) < MIN_TWAP_SAMPLES:
            return None

        total = sum(p["price"] for p in self._price_history)
        return round(total / len(self._price_history), 2)

    def _get_twap_days(self) -> float:
        """Get the number of days of TWAP data available."""
        if not self._price_history:
            return 0.0
        oldest = self._price_history[0]["ts"]
        now = int(time_mod.time())
        days = (now - oldest) / 86400
        return round(days, 1)

    async def _async_save_history(self) -> None:
        """Save price history to persistent storage."""
        try:
            await self._store.async_save({
                "price_history": self._price_history,
            })
        except Exception as e:
            _LOGGER.error("Error saving price history: %s", e)

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        # Save price history before shutdown
        if self._price_history:
            await self._async_save_history()

        # Clean up time listeners
        for unsub in self._unsub_time_listeners:
            unsub()
        self._unsub_time_listeners.clear()

        if self._session:
            await self._session.close()
            self._session = None
