"""Sensor entities for Flow Power HA integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_NEM_REGION,
    DOMAIN,
    SENSOR_TYPE_EXPORT_PRICE,
    SENSOR_TYPE_IMPORT_PRICE,
    SENSOR_TYPE_PRICE_FORECAST,
    SENSOR_TYPE_WHOLESALE_PRICE,
)
from .coordinator import FlowPowerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Flow Power sensors from a config entry."""
    coordinator: FlowPowerCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    region = config_entry.data.get(CONF_NEM_REGION, "NSW1")

    entities = [
        FlowPowerImportPriceSensor(coordinator, config_entry, region),
        FlowPowerExportPriceSensor(coordinator, config_entry, region),
        FlowPowerWholesaleSensor(coordinator, config_entry, region),
        FlowPowerForecastSensor(coordinator, config_entry, region),
    ]

    async_add_entities(entities)


class FlowPowerBaseSensor(CoordinatorEntity[FlowPowerCoordinator], SensorEntity):
    """Base class for Flow Power sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FlowPowerCoordinator,
        config_entry: ConfigEntry,
        region: str,
        sensor_type: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._region = region
        self._sensor_type = sensor_type
        self._attr_unique_id = f"{config_entry.entry_id}_{sensor_type}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry.entry_id)},
            "name": f"Flow Power ({region})",
            "manufacturer": "Flow Power",
            "model": "Electricity Pricing",
        }


class FlowPowerImportPriceSensor(FlowPowerBaseSensor):
    """Sensor for current import price (with PEA)."""

    _attr_name = "Import Price"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:currency-usd"

    def __init__(
        self,
        coordinator: FlowPowerCoordinator,
        config_entry: ConfigEntry,
        region: str,
    ) -> None:
        """Initialize the import price sensor."""
        super().__init__(coordinator, config_entry, region, SENSOR_TYPE_IMPORT_PRICE)

    @property
    def native_value(self) -> float | None:
        """Return the current import price in $/kWh."""
        if self.coordinator.data and self.coordinator.data.get("import_price"):
            return self.coordinator.data["import_price"].get("final_dollars")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = {
            "region": self._region,
            "unit": "$/kWh",
        }

        if self.coordinator.data and self.coordinator.data.get("import_price"):
            price_info = self.coordinator.data["import_price"]
            attrs.update({
                "price_cents": price_info.get("final_cents"),
                "base_rate_cents": price_info.get("base_rate"),
                "pea_cents": price_info.get("pea"),
                "wholesale_cents": price_info.get("wholesale"),
                "network_cents": price_info.get("network"),
                "gst_cents": price_info.get("gst"),
            })

        if self.coordinator.data:
            attrs["last_update"] = self.coordinator.data.get("last_update")

        return attrs


class FlowPowerExportPriceSensor(FlowPowerBaseSensor):
    """Sensor for current export price (Happy Hour aware)."""

    _attr_name = "Export Price"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power"

    def __init__(
        self,
        coordinator: FlowPowerCoordinator,
        config_entry: ConfigEntry,
        region: str,
    ) -> None:
        """Initialize the export price sensor."""
        super().__init__(coordinator, config_entry, region, SENSOR_TYPE_EXPORT_PRICE)

    @property
    def native_value(self) -> float | None:
        """Return the current export price in $/kWh."""
        if self.coordinator.data and self.coordinator.data.get("export_price"):
            return self.coordinator.data["export_price"].get("export_dollars")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = {
            "region": self._region,
            "unit": "$/kWh",
        }

        if self.coordinator.data and self.coordinator.data.get("export_price"):
            export_info = self.coordinator.data["export_price"]
            attrs.update({
                "price_cents": export_info.get("export_cents"),
                "is_happy_hour": export_info.get("is_happy_hour"),
                "happy_hour_rate": export_info.get("happy_hour_rate"),
                "happy_hour_start": export_info.get("happy_hour_start"),
                "happy_hour_end": export_info.get("happy_hour_end"),
            })

        return attrs


class FlowPowerWholesaleSensor(FlowPowerBaseSensor):
    """Sensor for raw wholesale price."""

    _attr_name = "Wholesale Price"
    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-line"

    def __init__(
        self,
        coordinator: FlowPowerCoordinator,
        config_entry: ConfigEntry,
        region: str,
    ) -> None:
        """Initialize the wholesale price sensor."""
        super().__init__(coordinator, config_entry, region, SENSOR_TYPE_WHOLESALE_PRICE)

    @property
    def native_value(self) -> float | None:
        """Return the current wholesale price in c/kWh."""
        if self.coordinator.data:
            return self.coordinator.data.get("wholesale_price")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = {
            "region": self._region,
            "unit": "c/kWh",
        }

        if self.coordinator.data:
            wholesale = self.coordinator.data.get("wholesale_price")
            if wholesale is not None:
                attrs["price_dollars"] = round(wholesale / 100, 4)
            attrs["last_update"] = self.coordinator.data.get("last_update")

        return attrs


class FlowPowerForecastSensor(FlowPowerBaseSensor):
    """Sensor for price forecast (EMHASS compatible)."""

    _attr_name = "Price Forecast"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(
        self,
        coordinator: FlowPowerCoordinator,
        config_entry: ConfigEntry,
        region: str,
    ) -> None:
        """Initialize the forecast sensor."""
        super().__init__(coordinator, config_entry, region, SENSOR_TYPE_PRICE_FORECAST)

    @property
    def native_value(self) -> float | None:
        """Return the current price (first forecast value) in $/kWh."""
        if self.coordinator.data and self.coordinator.data.get("import_price"):
            return self.coordinator.data["import_price"].get("final_dollars")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast data for EMHASS.

        EMHASS expects:
        - forecast: list of prices in $/kWh
        - timestamps: list of ISO timestamps
        """
        attrs = {
            "region": self._region,
            "unit": "$/kWh",
            "forecast": [],
            "timestamps": [],
            "forecast_cents": [],
            "wholesale_cents": [],
        }

        if self.coordinator.data and self.coordinator.data.get("forecast"):
            forecast = self.coordinator.data["forecast"]

            # Build EMHASS-compatible arrays
            prices = []
            timestamps = []
            prices_cents = []
            wholesale_cents = []

            for period in forecast:
                prices.append(period.get("price_dollars", 0))
                timestamps.append(period.get("timestamp", ""))
                prices_cents.append(period.get("price_cents", 0))
                wholesale_cents.append(period.get("wholesale_cents", 0))

            attrs["forecast"] = prices
            attrs["timestamps"] = timestamps
            attrs["forecast_cents"] = prices_cents
            attrs["wholesale_cents"] = wholesale_cents
            attrs["forecast_length"] = len(prices)

        if self.coordinator.data:
            attrs["last_update"] = self.coordinator.data.get("last_update")

        return attrs
