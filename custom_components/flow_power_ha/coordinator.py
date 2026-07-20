"""Data update coordinator for Flow Power HA."""
from __future__ import annotations

import logging
import time as time_mod
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_clients import AEMOClient
from .const import (
    CONF_BASE_RATE,
    CONF_FLOWPOWER_API_KEY,
    CONF_FLOWPOWER_NMI,
    CONF_HAPPY_HOUR_EXPORT_RATE,
    CONF_FP_NETWORK,
    CONF_FP_TARIFF_CODE,
    CONF_NEM_REGION,
    CONF_PEA_CUSTOM_VALUE,
    CONF_PEA_ENABLED,
    CONF_PRICE_SOURCE,
    DEFAULT_BASE_RATE,
    DEFAULT_TWAP_WINDOW_DAYS,
    DOMAIN,
    FLOWPOWER_KWATCH_REGIONS,
    MIN_TWAP_SAMPLES,
    NETWORK_API_NAME,
    PRICE_SOURCE_AEMO,
    PRICE_SOURCE_FLOWPOWER,
    UPDATE_INTERVAL_CURRENT,
    UPDATE_INTERVAL_FLOWPOWER,
)
from .flow_power_api import FlowPowerAPIClient, merge_price_forecasts
from .tariff_utils import compute_avg_daily_tariff, get_network_tariff_rate
from .pricing import (
    calculate_export_price,
    calculate_forecast_prices,
    calculate_import_price,
)
from .flow_power_pricing import FlowPowerPricingContext, resolve_flow_power_pricing_context

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Adaptive polling thresholds (seconds relative to the next 5-minute boundary)
# ---------------------------------------------------------------------------
# While waiting for the next boundary the coordinator checks infrequently.
# Close to the boundary it ramps up so new NEMWEB files are caught quickly.
_WAIT_INTERVAL = 45       # Poll interval while well away from the boundary (s)
_PRE_ACTIVE_WINDOW = 10   # Start gentle polling this many seconds before boundary
_PRE_ACTIVE_INTERVAL = 5  # Poll interval in the pre-active window (s)
_ACTIVE_WINDOW = 15       # Switch to rapid polling this many seconds after boundary
_ACTIVE_INTERVAL = 1      # Poll interval during active file search (s)


class FlowPowerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for fetching Flow Power price data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
    ) -> None:
        """Initialize the coordinator."""
        # Start with the fallback interval; update_interval is overridden
        # dynamically by the adaptive polling logic inside _async_update_data.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_CURRENT),
        )

        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._aemo_client: AEMOClient | None = None
        self._fp_api_client: FlowPowerAPIClient | None = None
        self._unsub_time_listeners: list = []

        # Configuration
        self.price_source = config.get(CONF_PRICE_SOURCE, PRICE_SOURCE_AEMO)
        self.region = config.get(CONF_NEM_REGION, "NSW1")
        self.base_rate = config.get(CONF_BASE_RATE, DEFAULT_BASE_RATE)
        self.pea_enabled = config.get(CONF_PEA_ENABLED, True)
        self.pea_custom_value = config.get(CONF_PEA_CUSTOM_VALUE)
        self.happy_hour_export_rate = config.get(CONF_HAPPY_HOUR_EXPORT_RATE)

        # Network tariff config
        self._fp_network = config.get(CONF_FP_NETWORK)
        self._fp_tariff_code = config.get(CONF_FP_TARIFF_CODE)
        self._network_tariff_rate: float | None = None
        self._avg_daily_tariff: float | None = None
        self._tariff_schedule: dict[int, float] | None = None

        # Flow Power Web Data API configuration.
        self.fp_api_key = config.get(CONF_FLOWPOWER_API_KEY)
        self.fp_nmi = config.get(CONF_FLOWPOWER_NMI)
        self.fp_api_enabled = bool(self.fp_api_key)

        # TWAP tracking
        self._price_history: list[dict[str, Any]] = []
        self._store = Store(hass, 1, f"{DOMAIN}.price_history.{self.region}")
        self._last_store_save: int | None = None
        self._twap: float | None = None

        # Import price history for ApexCharts: [[epoch_ms, cents], ...]
        self._import_price_history: list[list[int | float]] = []

        # Flow Power API account data
        self._fp_data: dict[str, Any] | None = None
        self._fp_last_fetch: float = 0

        # Persistent API account-data cache (survives restarts)
        self._fp_data_store = Store(hass, 1, f"{DOMAIN}.fp_account_data")

        # ------------------------------------------------------------------
        # Adaptive polling state
        # ------------------------------------------------------------------
        # Datetime of the next expected 5-minute dispatch boundary (naive
        # local time).  None until we receive the first dispatch timestamp.
        self._next_boundary: datetime | None = None
        # Current polling mode label — for log readability only.
        self._polling_mode: str = "active"  # Start active to get first data fast

        # Set up clock-aligned listeners for non-polling events
        self._setup_time_listeners()

    # ------------------------------------------------------------------
    # Time listeners (Happy Hour + tariff refresh)
    # ------------------------------------------------------------------

    def _setup_time_listeners(self) -> None:
        """Set up clock-aligned time listeners for Happy Hour and tariff refresh."""
        # Happy Hour transitions: Update exactly at 17:30:00 and 19:30:00
        unsub_happy_hour = async_track_time_change(
            self.hass,
            self._handle_happy_hour_update,
            hour=[17, 19],
            minute=[30],
            second=[0],
        )
        self._unsub_time_listeners.append(unsub_happy_hour)

        # Network tariff refresh: every 5 minutes
        if self._fp_network and self._fp_tariff_code:
            unsub_tariff = async_track_time_change(
                self.hass,
                self._handle_tariff_refresh,
                minute=[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55],
                second=[0],
            )
            self._unsub_time_listeners.append(unsub_tariff)

        _LOGGER.info(
            "Flow Power: Happy Hour listener registered at 17:30/19:30; "
            "adaptive polling replaces fixed 30-second AEMO timer"
        )

    @callback
    def _handle_happy_hour_update(self, now: datetime) -> None:
        """Handle Happy Hour transition update."""
        _LOGGER.info("Flow Power: Happy Hour transition update at %s", now)
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _handle_tariff_refresh(self, now: datetime) -> None:
        """Refresh the network tariff rate every 5 minutes."""
        if not self._fp_network or not self._fp_tariff_code:
            return
        api_name = NETWORK_API_NAME.get(self._fp_network)
        if not api_name:
            return

        async def _refresh() -> None:
            rate = await self.hass.async_add_executor_job(
                get_network_tariff_rate,
                datetime.now(timezone.utc),
                api_name,
                self._fp_tariff_code,
            )
            if rate is not None:
                self._network_tariff_rate = rate
                data = self._current_data_with_updated_tariff(rate)
                if data is not None:
                    self._publish_manual_data_update(data)
                _LOGGER.debug(
                    "Flow Power: Updated network tariff rate: %.4f c/kWh",
                    rate,
                )

        self.hass.async_create_task(_refresh())

    @callback
    def _publish_manual_data_update(self, data: dict[str, Any]) -> None:
        """Publish listener updates without rescheduling the main poll loop."""
        self.data = data
        self.last_update_success = True
        self.async_update_listeners()

    def _current_data_with_updated_tariff(
        self,
        network_tariff_rate: float,
    ) -> dict[str, Any] | None:
        """Return current coordinator data with import price recalculated."""
        if not self.data:
            return None

        data = dict(self.data)
        data["network_tariff_rate"] = network_tariff_rate
        data["avg_daily_tariff"] = self._avg_daily_tariff

        wholesale_cents = data.get("wholesale_price")
        if wholesale_cents is None and data.get("import_price"):
            wholesale_cents = data["import_price"].get("wholesale")

        if wholesale_cents is None:
            return data

        data["import_price"] = calculate_import_price(
            wholesale_cents=wholesale_cents,
            base_rate=self.base_rate,
            pea_enabled=self.pea_enabled,
            pea_custom_value=self.pea_custom_value,
            network_tariff_rate=network_tariff_rate,
            avg_daily_tariff=self._avg_daily_tariff,
            pricing_context=self._pricing_context(),
        )
        return data

    def _pricing_context(self) -> FlowPowerPricingContext:
        """Resolve the effective Flow Power pricing inputs for this update."""
        return resolve_flow_power_pricing_context(
            self.config,
            {},
            {
                "flow_power_twap_tracker": SimpleNamespace(twap=self._twap),
                "flow_power_account_data": self._fp_data,
            },
        )

    # ------------------------------------------------------------------
    # Adaptive polling helpers
    # ------------------------------------------------------------------

    def _parse_aemo_timestamp(self, timestamp_str: str) -> datetime | None:
        """Parse AEMO dispatch timestamp (always AEST UTC+10) to naive local datetime."""
        if not timestamp_str or "/" not in timestamp_str:
            return None
        try:
            from datetime import timezone as _tz, timedelta as _td
            aest = _tz(_td(hours=10))
            dt_naive = datetime.strptime(timestamp_str, "%Y/%m/%d %H:%M:%S")
            dt_aest = dt_naive.replace(tzinfo=aest)
            return dt_aest.astimezone().replace(tzinfo=None)
        except (ValueError, TypeError) as e:
            _LOGGER.debug("Failed to parse dispatch timestamp '%s': %s", timestamp_str, e)
            return None

    def _calc_next_boundary(self) -> datetime:
        """Return the next 5-minute wall-clock boundary from now (naive local)."""
        now = datetime.now()
        next_min = ((now.minute // 5) + 1) * 5
        if next_min >= 60:
            return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return now.replace(minute=next_min, second=0, microsecond=0)

    def _adjust_poll_interval(self) -> bool:
        """Set update_interval based on proximity to the next dispatch boundary.

        Three tiers:
          WAIT       (>10 s until boundary)  → 45 s intervals, skip NEMWEB fetch
          PRE-ACTIVE (−10 s … +15 s)         → 5 s intervals, fetch NEMWEB
          ACTIVE     (>15 s past boundary)   → 1 s intervals, fetch NEMWEB

        Returns True when we should actually hit NEMWEB this cycle, False when
        we should serve cached data and wait for the boundary.
        """
        if self._next_boundary is None:
            # No boundary known yet — poll now to get first data
            return True

        now = datetime.now()
        secs = (self._next_boundary - now).total_seconds()

        if secs > _PRE_ACTIVE_WINDOW:
            # WAIT mode — too early to expect a new file
            if self._polling_mode != "wait":
                self._polling_mode = "wait"
                _LOGGER.info(
                    "Flow Power: WAIT mode — next boundary %s in %ds",
                    self._next_boundary.strftime("%H:%M:%S"),
                    int(secs),
                )
            self.update_interval = timedelta(seconds=_WAIT_INTERVAL)
            return False

        if secs > -_ACTIVE_WINDOW:
            # PRE-ACTIVE mode — gently start checking
            if self._polling_mode != "pre-active":
                self._polling_mode = "pre-active"
                _LOGGER.info("Flow Power: PRE-ACTIVE mode (5 s intervals)")
            self.update_interval = timedelta(seconds=_PRE_ACTIVE_INTERVAL)
            return True

        # ACTIVE mode — new file could appear any second
        if self._polling_mode != "active":
            self._polling_mode = "active"
            _LOGGER.info("Flow Power: ACTIVE mode (1 s intervals) — searching for new dispatch file")
        self.update_interval = timedelta(seconds=_ACTIVE_INTERVAL)
        return True

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def _async_setup(self) -> None:
        """Set up the coordinator."""
        self._session = aiohttp.ClientSession()

        if self.price_source in (PRICE_SOURCE_AEMO, PRICE_SOURCE_FLOWPOWER):
            self._aemo_client = AEMOClient(self._session)

        if self.fp_api_enabled:
            self._fp_api_client = FlowPowerAPIClient(self.fp_api_key, self._session)

        # Initialise network tariff data if configured
        if self._fp_network and self._fp_tariff_code:
            api_name = NETWORK_API_NAME.get(self._fp_network)
            if api_name:
                self._avg_daily_tariff = await self.hass.async_add_executor_job(
                    compute_avg_daily_tariff, api_name, self._fp_tariff_code,
                )
                self._network_tariff_rate = await self.hass.async_add_executor_job(
                    get_network_tariff_rate,
                    datetime.now(timezone.utc),
                    api_name,
                    self._fp_tariff_code,
                )
                # Build tariff schedule: slot index (0-47) → tariff rate
                schedule: dict[int, float] = {}
                from zoneinfo import ZoneInfo

                aest = ZoneInfo("Australia/Sydney")
                base_date = datetime.now(aest).replace(
                    hour=0, minute=0, second=0, microsecond=0,
                )
                for slot in range(48):
                    slot_time = base_date + timedelta(minutes=slot * 30)
                    rate = await self.hass.async_add_executor_job(
                        get_network_tariff_rate,
                        slot_time,
                        api_name,
                        self._fp_tariff_code,
                    )
                    if rate is not None:
                        schedule[slot] = rate
                self._tariff_schedule = schedule if schedule else None

                _LOGGER.info(
                    "Flow Power: Network tariff initialised — network=%s, "
                    "tariff_code=%s, current_rate=%.4f c/kWh, "
                    "avg_daily=%.4f c/kWh, schedule_slots=%d",
                    self._fp_network,
                    self._fp_tariff_code,
                    self._network_tariff_rate if self._network_tariff_rate is not None else 0.0,
                    self._avg_daily_tariff if self._avg_daily_tariff is not None else 0.0,
                    len(schedule),
                )

        # Restore cached account data so sensors don't go unknown on restart
        if self.fp_api_enabled and not self._fp_data:
            cached = await self._fp_data_store.async_load()
            if cached and isinstance(cached.get("data"), dict):
                self._fp_data = cached["data"]
                self._fp_data["cached"] = True
                _LOGGER.info(
                    "Flow Power: Restored cached account data "
                    "(PEA=%.2f, TWAP=%.2f) — will refresh from the API",
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

    async def _save_fp_data_cache(self) -> None:
        """Persist the last known API account data so sensors survive restarts."""
        if not self._fp_data:
            return
        try:
            save_data = {k: v for k, v in self._fp_data.items() if k != "cached"}
            await self._fp_data_store.async_save({"data": save_data})
        except Exception as e:
            _LOGGER.error("Flow Power: Error saving API account data cache: %s", e)

    # ------------------------------------------------------------------
    # Main update loop
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API using adaptive polling.

        Polling strategy:
        - After receiving a new dispatch file: enter WAIT mode until just
          before the next 5-minute boundary (45 s check interval).
        - 10 s before the boundary: switch to PRE-ACTIVE (5 s interval).
        - 15 s after the boundary: switch to ACTIVE (1 s interval) and poll
          NEMWEB aggressively until a new file appears.
        - On new file: immediately return to WAIT mode.

        This mirrors the approach in the standalone AEMO NEMWEB integration
        and typically catches new dispatch prices within 1-3 s of publication,
        compared to up to 30 s with the previous fixed-interval approach.
        """
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
                "network_tariff_rate": self._network_tariff_rate,
                "avg_daily_tariff": self._avg_daily_tariff,
            }

            # Preserve existing values across polling cycles
            if self.data:
                data["import_price"] = self.data.get("import_price")
                data["wholesale_price"] = self.data.get("wholesale_price")
                data["forecast"] = self.data.get("forecast", [])
                data["last_update"] = self.data.get("last_update")

            # Account data is independent of dispatch timing.
            if self.fp_api_enabled and self.fp_nmi:
                await self._fetch_flowpower_api_data(data)

            # KWatch API pricing is the primary Flow Power path when an API key
            # is configured. If it fails, fall through to the existing AEMO path.
            if self.fp_api_enabled:
                if await self._fetch_kwatch_price_data(data):
                    data["export_price"] = calculate_export_price(
                        self.region,
                        happy_hour_rate_override=self.happy_hour_export_rate,
                    )
                    return data

            # Decide whether to hit NEMWEB this cycle.
            should_fetch = self._adjust_poll_interval()

            if not should_fetch:
                # Export price is time-based — keep it current even in WAIT mode.
                data["export_price"] = calculate_export_price(
                    self.region,
                    happy_hour_rate_override=self.happy_hour_export_rate,
                )
                return data

            # Fetch current prices based on source
            if self.price_source in (PRICE_SOURCE_AEMO, PRICE_SOURCE_FLOWPOWER) and self._aemo_client:
                current_prices, is_new_dispatch, _dispatch_file = (
                    await self._aemo_client.get_current_prices_with_file()
                )
                region_data = current_prices.get(self.region, {})

                if is_new_dispatch and region_data:
                    wholesale_cents = region_data.get("price_cents", 0)
                    timestamp = region_data.get("timestamp")

                    # Advance the boundary for the next cycle
                    if timestamp:
                        period_dt = self._parse_aemo_timestamp(timestamp)
                        if period_dt:
                            self._next_boundary = self._calc_next_boundary()
                            _LOGGER.info(
                                "Flow Power: New dispatch — price=%.2f c/kWh, "
                                "next boundary %s",
                                wholesale_cents,
                                self._next_boundary.strftime("%H:%M:%S"),
                            )

                    # Record wholesale price for TWAP calculation
                    self._record_price(wholesale_cents)
                    data["twap"] = self._twap
                    data["twap_days"] = self._get_twap_days()
                    data["twap_samples"] = len(self._price_history)

                    pricing_context = self._pricing_context()

                    import_info = calculate_import_price(
                        wholesale_cents=wholesale_cents,
                        base_rate=self.base_rate,
                        pea_enabled=self.pea_enabled,
                        pea_custom_value=self.pea_custom_value,
                        network_tariff_rate=self._network_tariff_rate,
                        avg_daily_tariff=self._avg_daily_tariff,
                        pricing_context=pricing_context,
                    )

                    data["import_price"] = import_info
                    data["wholesale_price"] = wholesale_cents
                    data["last_update"] = timestamp

                    # Track import price history for ApexCharts
                    epoch_ms = int(time_mod.time() * 1000)
                    price_cents = import_info.get("final_cents")
                    if price_cents is not None:
                        self._import_price_history.append([epoch_ms, price_cents])
                        if len(self._import_price_history) > 576:
                            self._import_price_history = self._import_price_history[-576:]

                    # Fetch forecast — gated on new dispatch so we don't hammer
                    # the predispatch endpoint every second during ACTIVE mode.
                    # The predispatch file itself only updates every ~30 minutes
                    # so the filename cache in AEMOClient handles deduplication.
                    forecast_raw, _is_new_pd, _pd_file = (
                        await self._aemo_client.get_price_forecast_with_file(
                            self.region, periods=96
                        )
                    )
                    _LOGGER.info(
                        "AEMO forecast raw periods: %d for %s",
                        len(forecast_raw) if forecast_raw else 0,
                        self.region,
                    )

                    if forecast_raw:
                        data["forecast"] = calculate_forecast_prices(
                            forecast_raw,
                            base_rate=self.base_rate,
                            pea_enabled=self.pea_enabled,
                            pea_custom_value=self.pea_custom_value,
                            tariff_schedule=self._tariff_schedule,
                            avg_daily_tariff=self._avg_daily_tariff,
                            pricing_context=pricing_context,
                        )
                        _LOGGER.info("Calculated forecast periods: %d", len(data["forecast"]))

                elif not is_new_dispatch and self._next_boundary is None and region_data:
                    # First run — file already cached but we still need a boundary.
                    # Only set it if we are not already past the next 5-minute mark;
                    # if we are, stay in ACTIVE mode so we immediately poll for the
                    # new file rather than sleeping until a boundary that has passed.
                    timestamp = region_data.get("timestamp")
                    if timestamp:
                        period_dt = self._parse_aemo_timestamp(timestamp)
                        if period_dt:
                            candidate = self._calc_next_boundary()
                            secs_until = (candidate - datetime.now()).total_seconds()
                            if secs_until > -_ACTIVE_WINDOW:
                                # Boundary is still in the future (or only just past) —
                                # safe to set; the tier logic will pick the right mode.
                                self._next_boundary = candidate
                                _LOGGER.info(
                                    "Flow Power: Boundary initialised from cached dispatch: "
                                    "next=%s (in %.0fs)",
                                    self._next_boundary.strftime("%H:%M:%S"),
                                    secs_until,
                                )
                            else:
                                # We are well past the boundary — stay in ACTIVE mode
                                # so we keep polling for the file that's already due.
                                _LOGGER.info(
                                    "Flow Power: Cached dispatch boundary already passed "
                                    "(%.0fs ago) — staying in ACTIVE mode",
                                    -secs_until,
                                )

            # Export price is always recalculated (time-based)
            data["export_price"] = calculate_export_price(
                self.region,
                happy_hour_rate_override=self.happy_hour_export_rate,
            )

            return data

        except Exception as err:
            _LOGGER.error("Error fetching Flow Power data: %s", err)
            raise UpdateFailed(f"Error fetching data: {err}") from err

    # ------------------------------------------------------------------
    # Flow Power API data fetch
    # ------------------------------------------------------------------

    async def _fetch_kwatch_price_data(self, data: dict[str, Any]) -> bool:
        """Fetch current and forecast prices from the Flow Power KWatch API."""
        if not self._fp_api_client:
            return False

        api_region = FLOWPOWER_KWATCH_REGIONS.get(self.region, self.region.lower())
        try:
            dispatch = await self._fp_api_client.dispatch5mins(api_region, period=60)
            # Keep the first upcoming half-hour slot; period=2 skips it.
            forecast_30 = await self._fp_api_client.predispatch30mins(
                api_region,
                period=1,
            )
            forecast_5 = await self._fp_api_client.predispatch5mins(
                api_region,
                period=60,
            )
        except Exception as err:
            _LOGGER.warning(
                "Flow Power: KWatch price fetch failed, falling back to AEMO: %s",
                err,
            )
            return False

        if not dispatch:
            _LOGGER.warning("Flow Power: KWatch returned no dispatch prices")
            return False

        latest = dispatch[-1]
        wholesale_cents = latest.get("perKwh")
        if wholesale_cents is None:
            return False

        self._record_price(wholesale_cents)
        data["twap"] = self._twap
        data["twap_days"] = self._get_twap_days()
        data["twap_samples"] = len(self._price_history)
        pricing_context = self._pricing_context()

        import_info = calculate_import_price(
            wholesale_cents=wholesale_cents,
            base_rate=self.base_rate,
            pea_enabled=self.pea_enabled,
            pea_custom_value=self.pea_custom_value,
            network_tariff_rate=self._network_tariff_rate,
            avg_daily_tariff=self._avg_daily_tariff,
            pricing_context=pricing_context,
        )

        data["import_price"] = import_info
        data["wholesale_price"] = wholesale_cents
        data["last_update"] = latest.get("nemTime")
        data["price_source"] = "flowpower_kwatch"

        epoch_ms = int(time_mod.time() * 1000)
        price_cents = import_info.get("final_cents")
        if price_cents is not None:
            self._import_price_history.append([epoch_ms, price_cents])
            if len(self._import_price_history) > 576:
                self._import_price_history = self._import_price_history[-576:]

        forecast_raw = merge_price_forecasts(forecast_5, forecast_30)
        if forecast_raw:
            data["forecast"] = calculate_forecast_prices(
                forecast_raw,
                base_rate=self.base_rate,
                pea_enabled=self.pea_enabled,
                pea_custom_value=self.pea_custom_value,
                tariff_schedule=self._tariff_schedule,
                avg_daily_tariff=self._avg_daily_tariff,
                pricing_context=pricing_context,
            )

        self._next_boundary = self._calc_next_boundary()
        self.update_interval = timedelta(seconds=UPDATE_INTERVAL_CURRENT)
        _LOGGER.info(
            "Flow Power: KWatch update — price=%.2f c/kWh, forecast_periods=%d",
            wholesale_cents,
            len(data.get("forecast", [])),
        )
        return True

    async def _fetch_flowpower_api_data(self, data: dict[str, Any]) -> None:
        """Fetch account data from the Flow Power KWatch API."""
        if not self._fp_api_client or not self.fp_nmi:
            if self._fp_data:
                data["flowpower_data"] = self._fp_data
            return

        now = time_mod.time()
        if now - self._fp_last_fetch < UPDATE_INTERVAL_FLOWPOWER and self._fp_data:
            data["flowpower_data"] = self._fp_data
            return

        try:
            account_data = await self._fp_api_client.get_residential_site_summary(
                self.fp_nmi
            )
        except Exception as err:
            _LOGGER.warning("Flow Power: KWatch account fetch failed: %s", err)
            if self._fp_data:
                data["flowpower_data"] = self._fp_data
            return

        if account_data:
            account_data.pop("cached", None)
            self._fp_data = account_data
            self._fp_last_fetch = now
            data["flowpower_data"] = account_data
            _LOGGER.info(
                "Flow Power: KWatch account data updated - TWAP=%.2f, PEA=%.2f, LWAP=%.2f",
                account_data.get("twap", 0),
                account_data.get("pea_actual", 0),
                account_data.get("lwap", 0),
            )
            await self._save_fp_data_cache()
        elif self._fp_data:
            data["flowpower_data"] = self._fp_data

    # ------------------------------------------------------------------
    # TWAP helpers (unchanged)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

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
