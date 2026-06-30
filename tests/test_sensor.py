from __future__ import annotations

import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components" / "flow_power_ha"


def _install_homeassistant_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor_mod = types.ModuleType("homeassistant.components.sensor")
    sensor_mod.SensorDeviceClass = SimpleNamespace(MONETARY="monetary")
    sensor_mod.SensorEntity = type("SensorEntity", (), {})
    sensor_mod.SensorStateClass = SimpleNamespace(MEASUREMENT="measurement")

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})

    const_mod = types.ModuleType("homeassistant.const")
    const_mod.UnitOfEnergy = SimpleNamespace(KILO_WATT_HOUR="kWh")

    core_mod = types.ModuleType("homeassistant.core")
    core_mod.HomeAssistant = type("HomeAssistant", (), {})
    core_mod.callback = lambda func: func

    helpers = types.ModuleType("homeassistant.helpers")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = type("AddEntitiesCallback", (), {})
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )
    update_coordinator.CoordinatorEntity = type(
        "CoordinatorEntity",
        (),
        {"__class_getitem__": classmethod(lambda cls, item: cls)},
    )

    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.components", components)
    sys.modules.setdefault("homeassistant.components.sensor", sensor_mod)
    sys.modules.setdefault("homeassistant.config_entries", config_entries)
    sys.modules.setdefault("homeassistant.const", const_mod)
    sys.modules.setdefault("homeassistant.core", core_mod)
    sys.modules.setdefault("homeassistant.helpers", helpers)
    sys.modules.setdefault(
        "homeassistant.helpers.entity_platform",
        entity_platform,
    )
    sys.modules.setdefault(
        "homeassistant.helpers.update_coordinator",
        update_coordinator,
    )


package = types.ModuleType("flow_power_ha")
package.__path__ = [str(COMPONENT_ROOT)]
sys.modules.setdefault("flow_power_ha", package)

coordinator_stub = types.ModuleType("flow_power_ha.coordinator")
coordinator_stub.FlowPowerCoordinator = type("FlowPowerCoordinator", (), {})
sys.modules.setdefault("flow_power_ha.coordinator", coordinator_stub)

_install_homeassistant_stubs()

from flow_power_ha.sensor import (  # noqa: E402
    FlowPowerBaseSensor,
    FlowPowerExportPriceSensor,
    FlowPowerForecastSensor,
)


def test_parse_timestamp_to_datetime_accepts_iso_values() -> None:
    sensor = object.__new__(FlowPowerBaseSensor)
    sensor._region = "QLD1"

    dt = sensor._parse_timestamp_to_datetime("2026-06-16T22:00:00")

    assert dt is not None
    assert dt.isoformat() == "2026-06-16T22:00:00+10:00"


def test_export_price_uses_configured_happy_hour_override() -> None:
    sensor = object.__new__(FlowPowerExportPriceSensor)
    sensor._region = "QLD1"
    sensor._config_entry = SimpleNamespace(
        data={},
        options={"happy_hour_export_rate": 0.5},
    )

    in_happy_hour = datetime.fromisoformat("2026-06-16T18:00:00+10:00")
    outside_happy_hour = datetime.fromisoformat("2026-06-16T20:00:00+10:00")

    assert sensor._get_export_price_for_time(in_happy_hour) == 0.5
    assert sensor._get_export_price_for_time(outside_happy_hour) == 0.0


def test_forecast_apex_series_uses_interval_start_timestamps() -> None:
    sensor = object.__new__(FlowPowerForecastSensor)
    sensor._region = "QLD1"
    sensor.coordinator = SimpleNamespace(
        data={
            "forecast": [
                {
                    "timestamp": "2026-06-30T07:30:00+10:00",
                    "duration_minutes": 30,
                    "price_dollars": 0.251,
                    "price_cents": 25.1,
                    "wholesale_cents": 10.2,
                },
                {
                    "timestamp": "2026-06-30T07:35:00+10:00",
                    "duration_minutes": 5,
                    "price_dollars": 0.261,
                    "price_cents": 26.1,
                    "wholesale_cents": 11.2,
                },
            ],
            "last_update": "2026-06-30T07:25:00+10:00",
        }
    )

    attrs = sensor.extra_state_attributes

    assert attrs["forecast_dict"]["2026-06-30T07:30:00+10:00"] == 0.251
    assert attrs["apex_forecast_import"] == [
        [int(datetime.fromisoformat("2026-06-30T07:00:00+10:00").timestamp() * 1000), 25.1],
        [int(datetime.fromisoformat("2026-06-30T07:30:00+10:00").timestamp() * 1000), 26.1],
    ]
    assert attrs["apex_forecast_wholesale"] == [
        [int(datetime.fromisoformat("2026-06-30T07:00:00+10:00").timestamp() * 1000), 10.2],
        [int(datetime.fromisoformat("2026-06-30T07:30:00+10:00").timestamp() * 1000), 11.2],
    ]
