"""Unit tests for FlowPowerCoordinator helper methods."""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components" / "flow_power_ha"


# ---------------------------------------------------------------------------
# Minimal Home Assistant stubs required to import coordinator.py
# ---------------------------------------------------------------------------

def _install_homeassistant_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")

    core_mod = types.ModuleType("homeassistant.core")
    core_mod.HomeAssistant = type("HomeAssistant", (), {})
    core_mod.callback = lambda func: func

    helpers = types.ModuleType("homeassistant.helpers")

    event_mod = types.ModuleType("homeassistant.helpers.event")
    event_mod.async_track_time_change = lambda *args, **kwargs: (lambda: None)

    storage_mod = types.ModuleType("homeassistant.helpers.storage")
    storage_mod.Store = type("Store", (), {"__init__": lambda self, *a, **kw: None})

    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class _FakeDataUpdateCoordinator:
        def __init__(self, hass, logger, *, name, update_interval):
            self.hass = hass
            self.data = None
            self.last_update_success = True
            self.update_interval = update_interval

        def async_update_listeners(self):
            pass

        def __class_getitem__(cls, item):
            return cls

    class _FakeCoordinatorEntity:
        def __class_getitem__(cls, item):
            return cls

    update_coordinator.DataUpdateCoordinator = _FakeDataUpdateCoordinator
    update_coordinator.CoordinatorEntity = _FakeCoordinatorEntity
    update_coordinator.UpdateFailed = Exception

    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.core", core_mod)
    sys.modules.setdefault("homeassistant.helpers", helpers)
    sys.modules.setdefault("homeassistant.helpers.event", event_mod)
    sys.modules.setdefault("homeassistant.helpers.storage", storage_mod)
    sys.modules.setdefault("homeassistant.helpers.update_coordinator", update_coordinator)

    aiohttp_mod = types.ModuleType("aiohttp")
    aiohttp_mod.ClientSession = type("ClientSession", (), {})
    sys.modules.setdefault("aiohttp", aiohttp_mod)

    # zoneinfo is a stdlib module (Python 3.9+) — do NOT stub it; let
    # pricing.py and other modules use the real ZoneInfo class.


_install_homeassistant_stubs()

package = types.ModuleType("flow_power_ha")
package.__path__ = [str(COMPONENT_ROOT)]
sys.modules.setdefault("flow_power_ha", package)

from flow_power_ha.coordinator import FlowPowerCoordinator  # noqa: E402
from flow_power_ha.const import CONF_EXPORT_SENSOR, PLAN_HAPPY_HOUR  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(value: str) -> SimpleNamespace:
    """Return a minimal HA state-like object."""
    return SimpleNamespace(state=value)


def _make_coordinator(
    export_sensor: str | None = "sensor.export_energy",
    export_baseline: float | None = None,
) -> FlowPowerCoordinator:
    """Construct a coordinator with a fake hass and the given export config."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=None)

    coordinator = FlowPowerCoordinator.__new__(FlowPowerCoordinator)
    # Bypass __init__ — set only the attributes exercised by the helpers under test.
    coordinator.hass = hass
    coordinator._export_sensor_entity_id = export_sensor
    coordinator._export_baseline = export_baseline
    return coordinator


# ---------------------------------------------------------------------------
# Tests for _capture_export_baseline  (Task 2.4)
# Requirements: 2.1, 2.2, 2.4, 6.2, 6.4, 6.5
# ---------------------------------------------------------------------------

class TestCaptureExportBaseline:
    """Tests for _capture_export_baseline."""

    def test_no_sensor_configured_leaves_baseline_none_and_skips_state_lookup(self) -> None:
        """Req 2.4: No export sensor → baseline stays None, hass.states never queried."""
        coordinator = _make_coordinator(export_sensor=None)

        coordinator._capture_export_baseline()

        assert coordinator._export_baseline is None
        coordinator.hass.states.get.assert_not_called()

    def test_sensor_state_unavailable_sets_baseline_none_and_logs_warning(
        self, caplog
    ) -> None:
        """Req 2.2, 6.2, 6.5: 'unavailable' state → baseline None, warning logged with entity_id."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator.hass.states.get.return_value = _make_state("unavailable")

        with caplog.at_level(logging.WARNING, logger="flow_power_ha.coordinator"):
            coordinator._capture_export_baseline()

        assert coordinator._export_baseline is None
        assert any(
            "sensor.export_energy" in msg for msg in caplog.messages
        ), f"Expected entity_id in warning, got: {caplog.messages}"

    def test_sensor_entity_absent_from_hass_states_sets_baseline_none_and_logs_warning(
        self, caplog
    ) -> None:
        """Req 2.2, 6.2, 6.5: Entity not in hass.states → baseline None, warning logged."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator.hass.states.get.return_value = None

        with caplog.at_level(logging.WARNING, logger="flow_power_ha.coordinator"):
            coordinator._capture_export_baseline()

        assert coordinator._export_baseline is None
        assert any(
            "sensor.export_energy" in msg for msg in caplog.messages
        ), f"Expected entity_id in warning, got: {caplog.messages}"

    def test_sensor_state_unknown_sets_baseline_none_and_logs_warning(
        self, caplog
    ) -> None:
        """Req 2.2, 6.5: 'unknown' state → baseline None, warning logged."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator.hass.states.get.return_value = _make_state("unknown")

        with caplog.at_level(logging.WARNING, logger="flow_power_ha.coordinator"):
            coordinator._capture_export_baseline()

        assert coordinator._export_baseline is None
        assert any(
            "sensor.export_energy" in msg for msg in caplog.messages
        )

    def test_non_numeric_state_sets_baseline_none(self) -> None:
        """Req 2.2, 6.4, 6.5: Non-numeric state → baseline None, no exception raised."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator.hass.states.get.return_value = _make_state("not_a_number")

        coordinator._capture_export_baseline()  # must not raise

        assert coordinator._export_baseline is None

    def test_negative_sensor_value_sets_baseline_none(self) -> None:
        """Req 2.2, 6.4: Negative reading → baseline None (negative cumulative makes no sense)."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator.hass.states.get.return_value = _make_state("-5.0")

        coordinator._capture_export_baseline()

        assert coordinator._export_baseline is None

    def test_valid_float_sensor_value_is_stored_as_baseline(self) -> None:
        """Req 2.1: Valid positive float → baseline stored correctly."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator.hass.states.get.return_value = _make_state("123.456")

        coordinator._capture_export_baseline()

        assert coordinator._export_baseline == 123.456

    def test_zero_is_a_valid_baseline(self) -> None:
        """Req 2.1: Reading of exactly 0.0 is valid (freshly reset meter) and stored."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator.hass.states.get.return_value = _make_state("0.0")

        coordinator._capture_export_baseline()

        assert coordinator._export_baseline == 0.0

    def test_baseline_is_overwritten_on_repeated_calls(self) -> None:
        """Req 2.1: Subsequent captures overwrite the previous baseline value."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator._export_baseline = 50.0
        coordinator.hass.states.get.return_value = _make_state("75.0")

        coordinator._capture_export_baseline()

        assert coordinator._export_baseline == 75.0

    def test_inf_sensor_value_sets_baseline_none(self) -> None:
        """Req 2.2, 6.4, 6.5: Non-finite float (inf) → baseline None, no exception raised."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator.hass.states.get.return_value = _make_state("inf")

        coordinator._capture_export_baseline()

        assert coordinator._export_baseline is None

    def test_nan_sensor_value_sets_baseline_none(self) -> None:
        """Req 2.2, 6.4, 6.5: Non-finite float (nan) → baseline None, no exception raised."""
        coordinator = _make_coordinator(export_sensor="sensor.export_energy")
        coordinator.hass.states.get.return_value = _make_state("nan")

        coordinator._capture_export_baseline()

        assert coordinator._export_baseline is None


# ---------------------------------------------------------------------------
# Tests for _compute_exported_this_window_kwh  (Task 2.5)
# Requirements: 3.1, 3.2, 3.3, 3.4, 6.3, 6.4, 6.5
# ---------------------------------------------------------------------------

class TestComputeExportedThisWindowKwh:
    """Tests for _compute_exported_this_window_kwh."""

    def test_returns_none_when_baseline_is_none(self) -> None:
        """Req 3.4, 6.3: No baseline → None returned; no state lookup attempted."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=None,
        )

        result = coordinator._compute_exported_this_window_kwh()

        assert result is None
        coordinator.hass.states.get.assert_not_called()

    def test_returns_none_when_no_sensor_configured(self) -> None:
        """Req 3.4, 6.1: No sensor configured → None returned."""
        coordinator = _make_coordinator(
            export_sensor=None,
            export_baseline=None,
        )

        result = coordinator._compute_exported_this_window_kwh()

        assert result is None

    def test_returns_none_when_sensor_entity_absent_from_hass_states(self) -> None:
        """Req 3.3, 6.3: Entity missing from hass.states → None returned without raising."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=10.0,
        )
        coordinator.hass.states.get.return_value = None

        result = coordinator._compute_exported_this_window_kwh()

        assert result is None
        coordinator.hass.states.get.assert_called_once_with("sensor.export_energy")

    def test_returns_none_when_sensor_state_is_unavailable(self) -> None:
        """Req 3.3, 6.5: 'unavailable' state → None returned without raising."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=10.0,
        )
        coordinator.hass.states.get.return_value = _make_state("unavailable")

        result = coordinator._compute_exported_this_window_kwh()

        assert result is None

    def test_returns_none_when_sensor_state_is_unknown(self) -> None:
        """Req 3.3, 6.5: 'unknown' state → None returned without raising."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=10.0,
        )
        coordinator.hass.states.get.return_value = _make_state("unknown")

        result = coordinator._compute_exported_this_window_kwh()

        assert result is None

    def test_returns_none_when_sensor_state_is_non_numeric_string(self) -> None:
        """Req 3.3, 6.4, 6.5: Non-numeric state → None returned without raising."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=10.0,
        )
        coordinator.hass.states.get.return_value = _make_state("not-a-number")

        result = coordinator._compute_exported_this_window_kwh()

        assert result is None

    def test_returns_none_when_computed_value_is_negative(self) -> None:
        """Req 3.2: Sensor rolled back below baseline → None returned (not negative)."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=50.0,
        )
        coordinator.hass.states.get.return_value = _make_state("48.0")

        result = coordinator._compute_exported_this_window_kwh()

        assert result is None

    def test_returns_zero_when_reading_equals_baseline(self) -> None:
        """Req 3.1: Reading equals baseline → 0.0 (no energy exported yet)."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=50.0,
        )
        coordinator.hass.states.get.return_value = _make_state("50.0")

        result = coordinator._compute_exported_this_window_kwh()

        assert result == 0.0

    def test_returns_correct_float_for_valid_reading(self) -> None:
        """Req 3.1: Valid reading above baseline → correct difference returned."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=100.0,
        )
        coordinator.hass.states.get.return_value = _make_state("107.5")

        result = coordinator._compute_exported_this_window_kwh()

        assert result == 7.5

    def test_returns_correct_float_at_cap_boundary(self) -> None:
        """Req 3.1: Reading exactly at the 15 kWh cap → 15.0 returned."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=200.0,
        )
        coordinator.hass.states.get.return_value = _make_state("215.0")

        result = coordinator._compute_exported_this_window_kwh()

        assert result == 15.0

    def test_does_not_raise_on_inf_sensor_value(self) -> None:
        """Req 6.5: Non-finite sensor state → None returned without raising."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=10.0,
        )
        coordinator.hass.states.get.return_value = _make_state("inf")

        result = coordinator._compute_exported_this_window_kwh()

        assert result is None

    def test_does_not_raise_on_nan_sensor_value(self) -> None:
        """Req 6.5: NaN sensor state → None returned without raising."""
        coordinator = _make_coordinator(
            export_sensor="sensor.export_energy",
            export_baseline=10.0,
        )
        coordinator.hass.states.get.return_value = _make_state("nan")

        result = coordinator._compute_exported_this_window_kwh()

        assert result is None


# ---------------------------------------------------------------------------
# Tests for _handle_plan_transition baseline integration  (Task 3.2)
# Requirements: 2.1, 2.3, 5.1, 5.2, 5.3
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402 (import after stubs are installed)
from unittest.mock import patch, call  # noqa: E402
from flow_power_ha.const import PLAN_LEGACY_HAPPY_HOUR, PLAN_4FREE  # noqa: E402


def _make_transition_coordinator(
    plan: str = PLAN_HAPPY_HOUR,
    export_sensor: str | None = "sensor.export_energy",
    export_baseline: float | None = None,
    wholesale_price: float | None = 20.0,
) -> FlowPowerCoordinator:
    """Construct a coordinator wired for _handle_plan_transition testing."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=SimpleNamespace(state="100.0"))
    hass.async_create_task = MagicMock()

    coordinator = FlowPowerCoordinator.__new__(FlowPowerCoordinator)
    coordinator.hass = hass
    coordinator._export_sensor_entity_id = export_sensor
    coordinator._export_baseline = export_baseline
    coordinator.plan = plan
    coordinator.region = "NSW1"
    coordinator.base_rate = 34.0
    coordinator.pea_enabled = False
    coordinator.pea_custom_value = None
    coordinator.happy_hour_export_rate = None
    coordinator._network_tariff_rate = None
    coordinator._avg_daily_tariff = None
    coordinator._tariff_schedule = None
    coordinator._twap = None
    coordinator._fp_data = None
    coordinator.config = {}
    coordinator.last_update_success = True
    # Provide existing coordinator data so the transition updates prices inline
    coordinator.data = {"wholesale_price": wholesale_price}
    # Track listener notifications
    coordinator._listener_call_count = 0

    def _fake_async_update_listeners():
        coordinator._listener_call_count += 1

    coordinator.async_update_listeners = _fake_async_update_listeners
    # Stub async_request_refresh (called when data is None at transition time)
    coordinator.async_request_refresh = MagicMock(return_value=None)
    return coordinator


class TestHandlePlanTransition:
    """Tests for _handle_plan_transition baseline integration."""

    # ------------------------------------------------------------------
    # 5.1 / 2.1 — Window-open at 17:30 captures baseline first
    # ------------------------------------------------------------------

    def test_transition_at_17_30_calls_capture_export_baseline(self) -> None:
        """Req 5.1, 2.1: At 17:30 _capture_export_baseline is called before price recalc."""
        coordinator = _make_transition_coordinator()
        # Sensor returns a valid numeric reading
        coordinator.hass.states.get.return_value = SimpleNamespace(state="150.0")

        now = datetime(2026, 8, 1, 17, 30, 0)
        coordinator._handle_plan_transition(now)

        assert coordinator._export_baseline == 150.0, (
            "Baseline should be set from the sensor reading at 17:30"
        )

    def test_transition_at_17_30_sets_baseline_before_publishing_price(self) -> None:
        """Req 5.1, 5.3: Baseline is captured before export price is published at 17:30."""
        coordinator = _make_transition_coordinator()
        coordinator.hass.states.get.return_value = SimpleNamespace(state="200.0")

        captured_baseline_at_publish: list[float | None] = []

        original_publish = FlowPowerCoordinator._publish_manual_data_update

        def _spy_publish(self, data):
            captured_baseline_at_publish.append(self._export_baseline)
            original_publish(self, data)

        with patch.object(FlowPowerCoordinator, "_publish_manual_data_update", _spy_publish):
            now = datetime(2026, 8, 1, 17, 30, 0)
            coordinator._handle_plan_transition(now)

        assert len(captured_baseline_at_publish) == 1
        assert captured_baseline_at_publish[0] == 200.0, (
            "Baseline must be set before _publish_manual_data_update is called"
        )

    def test_transition_at_17_30_sensor_unavailable_leaves_baseline_none(self) -> None:
        """Req 2.1, 2.2: Unavailable sensor at 17:30 → baseline stays None, no exception."""
        coordinator = _make_transition_coordinator()
        coordinator.hass.states.get.return_value = SimpleNamespace(state="unavailable")

        now = datetime(2026, 8, 1, 17, 30, 0)
        coordinator._handle_plan_transition(now)  # must not raise

        assert coordinator._export_baseline is None

    def test_transition_at_17_30_no_sensor_configured_leaves_baseline_none(self) -> None:
        """Req 2.4: No export sensor configured → baseline stays None at 17:30."""
        coordinator = _make_transition_coordinator(export_sensor=None)

        now = datetime(2026, 8, 1, 17, 30, 0)
        coordinator._handle_plan_transition(now)

        assert coordinator._export_baseline is None

    # ------------------------------------------------------------------
    # 5.2 / 2.3 — Window-close clears baseline to None
    # ------------------------------------------------------------------

    def test_transition_at_happy_hour_window_close_clears_baseline(self) -> None:
        """Req 5.2, 2.3: At 21:30 (happy_hour window-close) baseline is cleared to None."""
        coordinator = _make_transition_coordinator(
            plan=PLAN_HAPPY_HOUR,
            export_baseline=75.0,
        )

        now = datetime(2026, 8, 1, 21, 30, 0)
        coordinator._handle_plan_transition(now)

        assert coordinator._export_baseline is None

    def test_transition_at_legacy_happy_hour_window_close_clears_baseline(self) -> None:
        """Req 5.2, 2.3: At 19:30 (legacy_happy_hour window-close) baseline is cleared to None."""
        coordinator = _make_transition_coordinator(
            plan=PLAN_LEGACY_HAPPY_HOUR,
            export_baseline=50.0,
        )

        now = datetime(2026, 8, 1, 19, 30, 0)
        coordinator._handle_plan_transition(now)

        assert coordinator._export_baseline is None

    def test_transition_at_4free_window_close_clears_baseline(self) -> None:
        """Req 5.2, 2.3: At 21:30 (4free window-close) baseline is cleared to None."""
        coordinator = _make_transition_coordinator(
            plan=PLAN_4FREE,
            export_baseline=30.0,
        )

        now = datetime(2026, 8, 1, 21, 30, 0)
        coordinator._handle_plan_transition(now)

        assert coordinator._export_baseline is None

    def test_window_close_clears_baseline_before_publishing_price(self) -> None:
        """Req 5.2, 5.3: Baseline is cleared before export price is published at window-close."""
        coordinator = _make_transition_coordinator(
            plan=PLAN_HAPPY_HOUR,
            export_baseline=60.0,
        )

        captured_baseline_at_publish: list[float | None] = []

        original_publish = FlowPowerCoordinator._publish_manual_data_update

        def _spy_publish(self, data):
            captured_baseline_at_publish.append(self._export_baseline)
            original_publish(self, data)

        with patch.object(FlowPowerCoordinator, "_publish_manual_data_update", _spy_publish):
            now = datetime(2026, 8, 1, 21, 30, 0)
            coordinator._handle_plan_transition(now)

        assert len(captured_baseline_at_publish) == 1
        assert captured_baseline_at_publish[0] is None, (
            "Baseline must be None before _publish_manual_data_update is called at window-close"
        )

    # ------------------------------------------------------------------
    # 5.3 — Export price published immediately without waiting for next poll
    # ------------------------------------------------------------------

    def test_transition_at_17_30_calls_publish_manual_data_update(self) -> None:
        """Req 5.3: _publish_manual_data_update is called immediately at 17:30."""
        coordinator = _make_transition_coordinator()
        coordinator.hass.states.get.return_value = SimpleNamespace(state="100.0")

        publish_calls: list[dict] = []

        original_publish = FlowPowerCoordinator._publish_manual_data_update

        def _spy_publish(self, data):
            publish_calls.append(dict(data))
            original_publish(self, data)

        with patch.object(FlowPowerCoordinator, "_publish_manual_data_update", _spy_publish):
            now = datetime(2026, 8, 1, 17, 30, 0)
            coordinator._handle_plan_transition(now)

        assert len(publish_calls) == 1, (
            "_publish_manual_data_update should be called exactly once at 17:30"
        )

    def test_transition_at_window_close_calls_publish_manual_data_update(self) -> None:
        """Req 5.3: _publish_manual_data_update is called immediately at window-close."""
        coordinator = _make_transition_coordinator(
            plan=PLAN_HAPPY_HOUR,
            export_baseline=50.0,
        )

        publish_calls: list[dict] = []

        original_publish = FlowPowerCoordinator._publish_manual_data_update

        def _spy_publish(self, data):
            publish_calls.append(dict(data))
            original_publish(self, data)

        with patch.object(FlowPowerCoordinator, "_publish_manual_data_update", _spy_publish):
            now = datetime(2026, 8, 1, 21, 30, 0)
            coordinator._handle_plan_transition(now)

        assert len(publish_calls) == 1, (
            "_publish_manual_data_update should be called exactly once at window-close"
        )

    def test_publish_manual_data_update_notifies_listeners(self) -> None:
        """Req 5.3: async_update_listeners is invoked so downstream sensors see the new price."""
        coordinator = _make_transition_coordinator()
        coordinator.hass.states.get.return_value = SimpleNamespace(state="100.0")

        now = datetime(2026, 8, 1, 17, 30, 0)
        coordinator._handle_plan_transition(now)

        assert coordinator._listener_call_count >= 1, (
            "async_update_listeners must be called so HA sensors refresh immediately"
        )

    def test_transition_with_no_existing_data_does_not_raise(self) -> None:
        """Req 5.3, 6.5: If coordinator.data is None (before first poll), no exception raised."""
        coordinator = _make_transition_coordinator(wholesale_price=None)
        coordinator.data = None
        coordinator.hass.states.get.return_value = SimpleNamespace(state="100.0")

        now = datetime(2026, 8, 1, 17, 30, 0)
        coordinator._handle_plan_transition(now)  # must not raise


# ---------------------------------------------------------------------------
# Tests for coordinator initialisation  (Task 6.2)
# Requirements: 2.5
# ---------------------------------------------------------------------------

from flow_power_ha.const import CONF_EXPORT_SENSOR  # noqa: F811 (re-import is fine; already imported above)


class TestCoordinatorInitialisation:
    """Req 2.5: _export_baseline is None on startup before any transition fires."""

    def test_export_baseline_is_none_on_init_with_export_sensor_configured(self) -> None:
        """Req 2.5: Instantiating the coordinator with CONF_EXPORT_SENSOR set
        must leave _export_baseline as None — no transition has fired yet."""
        hass = MagicMock()
        # hass.states is not accessed during __init__; provide it for safety.
        hass.states = MagicMock()

        config = {
            CONF_EXPORT_SENSOR: "sensor.solar_export_energy",
        }

        coordinator = FlowPowerCoordinator(hass, config)

        assert coordinator._export_baseline is None, (
            "_export_baseline must start as None before the first 17:30 transition"
        )

    def test_export_sensor_entity_id_stored_correctly_on_init(self) -> None:
        """Supplementary: entity_id from config is stored during __init__."""
        hass = MagicMock()
        hass.states = MagicMock()

        config = {
            CONF_EXPORT_SENSOR: "sensor.solar_export_energy",
        }

        coordinator = FlowPowerCoordinator(hass, config)

        assert coordinator._export_sensor_entity_id == "sensor.solar_export_energy"

    def test_export_baseline_is_none_on_init_without_export_sensor(self) -> None:
        """Req 2.5: Coordinator without any export sensor also starts with baseline None."""
        hass = MagicMock()
        hass.states = MagicMock()

        coordinator = FlowPowerCoordinator(hass, {})

        assert coordinator._export_baseline is None
        assert coordinator._export_sensor_entity_id is None
