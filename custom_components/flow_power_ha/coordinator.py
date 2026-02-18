"""Data update coordinator for Flow Power HA."""
from __future__ import annotations

import logging
import time as time_mod
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_clients import AEMOClient, AmberClient
from .const import (
    CONF_AMBER_API_KEY,
    CONF_AMBER_SITE_ID,
    CONF_BASE_RATE,
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
    UPDATE_INTERVAL_CURRENT,
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

        # TWAP tracking
        self._price_history: list[dict[str, Any]] = []
        self._store = Store(hass, 1, f"{DOMAIN}.price_history.{self.region}")
        self._last_store_save: int | None = None
        self._twap: float | None = None

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
            }

            # Fetch current prices based on source
            if self.price_source == PRICE_SOURCE_AEMO and self._aemo_client:
                current_prices = await self._aemo_client.get_current_prices()
                region_data = current_prices.get(self.region, {})

                if region_data:
                    wholesale_cents = region_data.get("price_cents", 0)

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
                    data["last_update"] = region_data.get("timestamp")

                # Fetch forecast
                forecast_raw = await self._aemo_client.get_price_forecast(
                    self.region, periods=96
                )
                _LOGGER.info("AEMO forecast raw periods: %d for %s", len(forecast_raw) if forecast_raw else 0, self.region)
                if forecast_raw:
                    data["forecast"] = calculate_forecast_prices(
                        forecast_raw,
                        base_rate=self.base_rate,
                        pea_enabled=self.pea_enabled,
                        pea_custom_value=self.pea_custom_value,
                        twap=self._twap,
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
