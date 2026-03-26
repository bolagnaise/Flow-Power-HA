"""Sensor entities for Flow Power HA integration."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

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
    CONF_FLOWPOWER_EMAIL,
    CONF_FP_NETWORK,
    CONF_NEM_REGION,
    CONF_PRICE_SOURCE,
    DOMAIN,
    FLOW_POWER_EXPORT_RATES,
    FLOW_POWER_MARKET_AVG,
    HAPPY_HOUR_END,
    HAPPY_HOUR_START,
    PRICE_SOURCE_FLOWPOWER,
    SENSOR_TYPE_EXPORT_PRICE,
    SENSOR_TYPE_FLOWPOWER_ACCOUNT,
    SENSOR_TYPE_IMPORT_PRICE,
    SENSOR_TYPE_NETWORK_TARIFF,
    SENSOR_TYPE_PRICE_FORECAST,
    SENSOR_TYPE_TWAP,
    SENSOR_TYPE_WHOLESALE_PRICE,
)
from .coordinator import FlowPowerCoordinator

_LOGGER = logging.getLogger(__name__)

# Region timezone mapping for ISO timestamp conversion
REGION_TIMEZONES = {
    "NSW1": "Australia/Sydney",
    "QLD1": "Australia/Brisbane",
    "VIC1": "Australia/Melbourne",
    "SA1": "Australia/Adelaide",
    "TAS1": "Australia/Hobart",
}


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
        FlowPowerTWAPSensor(coordinator, config_entry, region),
    ]

    # Add Flow Power portal account sensor when portal credentials are configured
    merged = {**config_entry.data, **config_entry.options}
    if merged.get(CONF_FLOWPOWER_EMAIL):
        entities.append(
            FlowPowerAccountSensor(coordinator, config_entry, region)
        )

    # Add network tariff sensor when a network is configured
    if merged.get(CONF_FP_NETWORK):
        entities.append(
            FlowPowerNetworkTariffSensor(coordinator, config_entry, region)
        )

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

    def _convert_to_iso_timestamp(self, timestamp: str) -> str:
        """Convert AEMO timestamp format to ISO format with timezone.

        Args:
            timestamp: AEMO format '2025/12/22 09:30:00' or ISO format

        Returns:
            ISO format '2025-12-22 09:30:00+10:00'
        """
        if not timestamp:
            return ""

        try:
            tz_name = REGION_TIMEZONES.get(self._region, "Australia/Sydney")
            tz = ZoneInfo(tz_name)

            if "/" in timestamp:
                dt = datetime.strptime(timestamp, "%Y/%m/%d %H:%M:%S")
                dt = dt.replace(tzinfo=tz)
            else:
                return timestamp

            return dt.strftime("%Y-%m-%d %H:%M:%S%z")
        except (ValueError, TypeError):
            return timestamp

    def _parse_timestamp_to_datetime(self, timestamp: str) -> datetime | None:
        """Parse timestamp string to datetime with timezone."""
        if not timestamp:
            return None

        try:
            tz_name = REGION_TIMEZONES.get(self._region, "Australia/Sydney")
            tz = ZoneInfo(tz_name)

            if "/" in timestamp:
                dt = datetime.strptime(timestamp, "%Y/%m/%d %H:%M:%S")
                return dt.replace(tzinfo=tz)
            return None
        except (ValueError, TypeError):
            return None


class FlowPowerImportPriceSensor(FlowPowerBaseSensor):
    """Sensor for current import price (with PEA)."""

    _attr_name = "Import Price"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
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
        """Return additional attributes with EMHASS-compatible forecast_dict."""
        attrs = {
            "region": self._region,
            "unit": "$/kWh",
            "forecast_dict": {},
        }

        if self.coordinator.data and self.coordinator.data.get("import_price"):
            price_info = self.coordinator.data["import_price"]
            attrs.update({
                "price_cents": price_info.get("final_cents"),
                "base_rate_cents": price_info.get("base_rate"),
                "pea_cents": price_info.get("pea"),
                "wholesale_cents": price_info.get("wholesale"),
                "twap_used": price_info.get("twap_used"),
                "network_cents": price_info.get("network"),
                "gst_cents": price_info.get("gst"),
            })

        # Build forecast_dict for EMHASS
        if self.coordinator.data and self.coordinator.data.get("forecast"):
            forecast_dict = {}
            for period in self.coordinator.data["forecast"]:
                raw_ts = period.get("timestamp", "")
                iso_ts = self._convert_to_iso_timestamp(raw_ts)
                if iso_ts:
                    forecast_dict[iso_ts] = period.get("price_dollars", 0)
            attrs["forecast_dict"] = forecast_dict

        if self.coordinator.data:
            attrs["last_update"] = self.coordinator.data.get("last_update")
            attrs["formula_version"] = (
                "v2" if self.coordinator.data.get("network_tariff_rate") is not None
                else "legacy"
            )

        return attrs


class FlowPowerExportPriceSensor(FlowPowerBaseSensor):
    """Sensor for current export price (Happy Hour aware)."""

    _attr_name = "Export Price"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:solar-power"

    def __init__(
        self,
        coordinator: FlowPowerCoordinator,
        config_entry: ConfigEntry,
        region: str,
    ) -> None:
        """Initialize the export price sensor."""
        super().__init__(coordinator, config_entry, region, SENSOR_TYPE_EXPORT_PRICE)

    def _get_export_price_for_time(self, dt: datetime) -> float:
        """Calculate export price for a specific time (Happy Hour aware)."""
        local_time = dt.time()
        is_happy_hour = HAPPY_HOUR_START <= local_time < HAPPY_HOUR_END
        if is_happy_hour:
            return FLOW_POWER_EXPORT_RATES.get(self._region, 0.0)
        return 0.0

    @property
    def native_value(self) -> float | None:
        """Return the current export price in $/kWh."""
        if self.coordinator.data and self.coordinator.data.get("export_price"):
            return self.coordinator.data["export_price"].get("export_dollars")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes with EMHASS-compatible forecast_dict."""
        attrs = {
            "region": self._region,
            "unit": "$/kWh",
            "forecast_dict": {},
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

        # Build forecast_dict for EMHASS (export prices based on Happy Hour)
        if self.coordinator.data and self.coordinator.data.get("forecast"):
            forecast_dict = {}
            for period in self.coordinator.data["forecast"]:
                raw_ts = period.get("timestamp", "")
                iso_ts = self._convert_to_iso_timestamp(raw_ts)
                dt = self._parse_timestamp_to_datetime(raw_ts)
                if iso_ts and dt:
                    export_price = self._get_export_price_for_time(dt)
                    forecast_dict[iso_ts] = export_price
            attrs["forecast_dict"] = forecast_dict

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
        """Return additional attributes with EMHASS-compatible forecast_dict."""
        attrs = {
            "region": self._region,
            "unit": "c/kWh",
            "forecast_dict": {},
        }

        if self.coordinator.data:
            wholesale = self.coordinator.data.get("wholesale_price")
            if wholesale is not None:
                attrs["price_dollars"] = round(wholesale / 100, 4)
            attrs["last_update"] = self.coordinator.data.get("last_update")

        # Build forecast_dict for EMHASS (wholesale in $/kWh)
        if self.coordinator.data and self.coordinator.data.get("forecast"):
            forecast_dict = {}
            for period in self.coordinator.data["forecast"]:
                raw_ts = period.get("timestamp", "")
                iso_ts = self._convert_to_iso_timestamp(raw_ts)
                if iso_ts:
                    # Convert c/kWh to $/kWh
                    wholesale_cents = period.get("wholesale_cents", 0)
                    forecast_dict[iso_ts] = round(wholesale_cents / 100, 4)
            attrs["forecast_dict"] = forecast_dict

        return attrs


class FlowPowerForecastSensor(FlowPowerBaseSensor):
    """Sensor for price forecast (EMHASS compatible)."""

    _attr_name = "Price Forecast"
    _attr_native_unit_of_measurement = "$/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY
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
        """Return the next forecast period price in $/kWh.

        Uses the second forecast period (index 1) so this sensor shows
        the upcoming price rather than mirroring the current import sensor.
        Falls back to the first period if only one is available.
        """
        if self.coordinator.data and self.coordinator.data.get("forecast"):
            forecast = self.coordinator.data["forecast"]
            # Prefer next period (index 1) over current (index 0)
            idx = 1 if len(forecast) > 1 else 0
            return forecast[idx].get("price_dollars")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return forecast data for EMHASS.

        EMHASS expects either:
        - forecast: list of prices in $/kWh (list format)
        - forecast_dict: dict mapping ISO timestamps to prices (dict format)
        """
        attrs = {
            "region": self._region,
            "unit": "$/kWh",
            "forecast": [],
            "timestamps": [],
            "forecast_dict": {},
            "forecast_cents": [],
            "wholesale_cents": [],
        }

        if self.coordinator.data and self.coordinator.data.get("forecast"):
            forecast = self.coordinator.data["forecast"]

            # Build EMHASS-compatible arrays and dict
            prices = []
            timestamps = []
            forecast_dict = {}
            prices_cents = []
            wholesale_cents = []

            for period in forecast:
                price = period.get("price_dollars", 0)
                raw_ts = period.get("timestamp", "")
                iso_ts = self._convert_to_iso_timestamp(raw_ts)

                prices.append(price)
                timestamps.append(iso_ts)
                prices_cents.append(period.get("price_cents", 0))
                wholesale_cents.append(period.get("wholesale_cents", 0))

                # Build dictionary format for EMHASS
                if iso_ts:
                    forecast_dict[iso_ts] = price

            attrs["forecast"] = prices
            attrs["timestamps"] = timestamps
            attrs["forecast_dict"] = forecast_dict
            attrs["forecast_cents"] = prices_cents
            attrs["wholesale_cents"] = wholesale_cents
            attrs["forecast_length"] = len(prices)

            # Pre-built ApexCharts series: [[epoch_ms, cents], ...]
            apex_import = []
            apex_wholesale = []
            for period in forecast:
                raw_ts = period.get("timestamp", "")
                dt = self._parse_timestamp_to_datetime(raw_ts)
                if dt:
                    epoch_ms = int(dt.timestamp() * 1000)
                    apex_import.append(
                        [epoch_ms, period.get("price_cents", 0)]
                    )
                    apex_wholesale.append(
                        [epoch_ms, period.get("wholesale_cents", 0)]
                    )
            attrs["apex_forecast_import"] = apex_import
            attrs["apex_forecast_wholesale"] = apex_wholesale

        if self.coordinator.data:
            attrs["last_update"] = self.coordinator.data.get("last_update")

        return attrs


class FlowPowerTWAPSensor(FlowPowerBaseSensor):
    """Sensor for 30-day Time Weighted Average Price (TWAP)."""

    _attr_name = "TWAP (30-day Average)"
    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-timeline-variant-shimmer"

    def __init__(
        self,
        coordinator: FlowPowerCoordinator,
        config_entry: ConfigEntry,
        region: str,
    ) -> None:
        """Initialize the TWAP sensor."""
        super().__init__(coordinator, config_entry, region, SENSOR_TYPE_TWAP)

    @property
    def native_value(self) -> float | None:
        """Return the current TWAP in c/kWh."""
        if self.coordinator.data:
            return self.coordinator.data.get("twap")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return TWAP metadata."""
        attrs = {
            "region": self._region,
            "unit": "c/kWh",
            "window_days": 30,
            "default_market_avg": FLOW_POWER_MARKET_AVG,
        }

        if self.coordinator.data:
            twap = self.coordinator.data.get("twap")
            days = self.coordinator.data.get("twap_days", 0)
            samples = self.coordinator.data.get("twap_samples", 0)

            attrs["days_of_data"] = days
            attrs["sample_count"] = samples
            attrs["using_fallback"] = twap is None

            if twap is not None:
                attrs["twap_dollars"] = round(twap / 100, 4)

        return attrs


class FlowPowerAccountSensor(FlowPowerBaseSensor):
    """Sensor for actual Flow Power account data from the portal.

    Exposes the real PEA, LWAP, TWAP, and other account-specific values
    directly from Flow Power's billing system, rather than calculated estimates.
    """

    _attr_name = "Account PEA (Actual)"
    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-cash"

    def __init__(
        self,
        coordinator: FlowPowerCoordinator,
        config_entry: ConfigEntry,
        region: str,
    ) -> None:
        """Initialize the Flow Power account sensor."""
        super().__init__(
            coordinator, config_entry, region, SENSOR_TYPE_FLOWPOWER_ACCOUNT
        )

    @property
    def native_value(self) -> float | None:
        """Return the actual PEA from Flow Power."""
        if self.coordinator.data:
            fp_data = self.coordinator.data.get("flowpower_data")
            if fp_data:
                return fp_data.get("pea_actual")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all Flow Power account metrics as attributes."""
        attrs: dict[str, Any] = {"region": self._region}

        if not self.coordinator.data:
            return attrs

        fp_data = self.coordinator.data.get("flowpower_data")
        if not fp_data:
            attrs["status"] = "unavailable"
            return attrs

        attrs["status"] = "cached" if fp_data.get("cached") else "live"
        attrs.update({
            "lwap": fp_data.get("lwap"),
            "lwap_import": fp_data.get("lwap_import"),
            "lwap_actual": fp_data.get("lwap_actual"),
            "lwap_import_actual": fp_data.get("lwap_import_actual"),
            "twap": fp_data.get("twap"),
            "twap_import": fp_data.get("twap_import"),
            "avg_rrp": fp_data.get("avg_rrp"),
            "pea_30_days": fp_data.get("pea_30_days"),
            "pea_30_import": fp_data.get("pea_30_import"),
            "pea_actual": fp_data.get("pea_actual"),
            "pea_target": fp_data.get("pea_target"),
            "pea_actual_import": fp_data.get("pea_actual_import"),
            "pea_target_import": fp_data.get("pea_target_import"),
            "bpea": fp_data.get("bpea"),
            "bpea_import": fp_data.get("bpea_import"),
            "cpea": fp_data.get("cpea"),
            "cpea_import": fp_data.get("cpea_import"),
            "site_losses_dlf": fp_data.get("site_losses_dlf"),
            "gst_multiplier": fp_data.get("gst_multiplier"),
            "avg_usage_kw": fp_data.get("avg_usage_kw"),
            "avg_import_usage_kw": fp_data.get("avg_import_usage_kw"),
            "max_usage_kw": fp_data.get("max_usage_kw"),
        })

        return attrs


class FlowPowerNetworkTariffSensor(FlowPowerBaseSensor):
    """Sensor for current network tariff rate."""

    _attr_name = "Network Tariff"
    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator, config_entry, region):
        super().__init__(coordinator, config_entry, region, SENSOR_TYPE_NETWORK_TARIFF)

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("network_tariff_rate")
        return None

    @property
    def extra_state_attributes(self):
        attrs = {"region": self._region}
        if self.coordinator.data:
            attrs["avg_daily_tariff"] = self.coordinator.data.get("avg_daily_tariff")
            attrs["network"] = self.coordinator.data.get("fp_network")
            attrs["tariff_code"] = self.coordinator.data.get("fp_tariff_code")
        return attrs
