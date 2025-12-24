"""Data update coordinator for Flow Power HA."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
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
    DOMAIN,
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


class FlowPowerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for fetching Flow Power price data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
    ) -> None:
        """Initialize the coordinator."""
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

        # Configuration
        self.price_source = config.get(CONF_PRICE_SOURCE, PRICE_SOURCE_AEMO)
        self.region = config.get(CONF_NEM_REGION, "NSW1")
        self.base_rate = config.get(CONF_BASE_RATE, DEFAULT_BASE_RATE)
        self.pea_enabled = config.get(CONF_PEA_ENABLED, True)
        self.pea_custom_value = config.get(CONF_PEA_CUSTOM_VALUE)

        # Amber config
        self.amber_api_key = config.get(CONF_AMBER_API_KEY)
        self.amber_site_id = config.get(CONF_AMBER_SITE_ID)

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
            }

            # Fetch current prices based on source
            if self.price_source == PRICE_SOURCE_AEMO and self._aemo_client:
                current_prices = await self._aemo_client.get_current_prices()
                region_data = current_prices.get(self.region, {})

                if region_data:
                    wholesale_cents = region_data.get("price_cents", 0)

                    # Calculate import price
                    import_info = calculate_import_price(
                        wholesale_cents=wholesale_cents,
                        base_rate=self.base_rate,
                        pea_enabled=self.pea_enabled,
                        pea_custom_value=self.pea_custom_value,
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
                    )
                    _LOGGER.info("Calculated forecast periods: %d", len(data["forecast"]))

            elif self.price_source == PRICE_SOURCE_AMBER and self._amber_client:
                current_prices = await self._amber_client.get_current_prices()

                # Find general (import) channel
                for price in current_prices:
                    if price.get("channelType") == "general":
                        wholesale_cents = self._amber_client.extract_wholesale_price(price)

                        # Calculate import price
                        import_info = calculate_import_price(
                            wholesale_cents=wholesale_cents,
                            base_rate=self.base_rate,
                            pea_enabled=self.pea_enabled,
                            pea_custom_value=self.pea_custom_value,
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
                    )

            # Calculate export price (always based on region and time)
            data["export_price"] = calculate_export_price(self.region)

            return data

        except Exception as err:
            _LOGGER.error("Error fetching Flow Power data: %s", err)
            raise UpdateFailed(f"Error fetching data: {err}") from err

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        if self._session:
            await self._session.close()
            self._session = None
