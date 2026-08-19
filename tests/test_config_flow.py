"""Unit tests for CONF_EXPORT_SENSOR wiring in config and options flows.

Validates requirements 1.1, 1.2, 1.3, 1.4, and 1.5.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import voluptuous as vol


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components" / "flow_power_ha"


# ---------------------------------------------------------------------------
# Minimal Home Assistant stubs — only what config_flow.py needs at import time
# ---------------------------------------------------------------------------

def _install_homeassistant_stubs() -> None:
    """Install minimal HA stubs so config_flow can be imported."""

    homeassistant = types.ModuleType("homeassistant")
    config_entries_mod = types.ModuleType("homeassistant.config_entries")

    class _FakeConfigFlow:
        """Minimal ConfigFlow base."""
        VERSION = 1

        def __init_subclass__(cls, domain=None, **kwargs):  # noqa: D102
            super().__init_subclass__(**kwargs)

        async def async_show_form(self, **kwargs):  # noqa: D102
            return kwargs

        async def async_create_entry(self, **kwargs):  # noqa: D102
            return kwargs

    class _FakeOptionsFlow:
        """Minimal OptionsFlow base."""
        async def async_show_form(self, **kwargs):  # noqa: D102
            return kwargs

        async def async_create_entry(self, **kwargs):  # noqa: D102
            return kwargs

    config_entries_mod.ConfigFlow = _FakeConfigFlow
    config_entries_mod.OptionsFlow = _FakeOptionsFlow
    config_entries_mod.ConfigEntry = type("ConfigEntry", (), {})

    core_mod = types.ModuleType("homeassistant.core")
    core_mod.HomeAssistant = type("HomeAssistant", (), {})
    core_mod.callback = lambda func: func

    data_entry_flow_mod = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow_mod.FlowResult = dict

    # selector stubs — must be callable so vol.Schema accepts them as validators.
    # Each selector acts as a pass-through: it returns whatever value it receives.
    selector_mod = types.ModuleType("homeassistant.helpers.selector")

    class _EntitySelectorConfig:
        def __init__(self, **kwargs):
            self.domain = kwargs.get("domain")

    class _EntitySelector:
        def __init__(self, config=None):
            self.config = config

        def __call__(self, value):  # pass-through validator
            return value

    class _NumberSelectorConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _NumberSelector:
        def __init__(self, config=None):
            self.config = config

        def __call__(self, value):
            return value

    class _SelectSelectorConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _SelectSelector:
        def __init__(self, config=None):
            self.config = config

        def __call__(self, value):
            return value

    class _TextSelectorConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _TextSelector:
        def __init__(self, config=None):
            self.config = config

        def __call__(self, value):
            return value

    class _SelectOptionDict(dict):
        def __new__(cls, value=None, label=None):
            instance = super().__new__(cls, value=value, label=label)
            return instance

    selector_mod.EntitySelectorConfig = _EntitySelectorConfig
    selector_mod.EntitySelector = _EntitySelector
    selector_mod.NumberSelectorConfig = _NumberSelectorConfig
    selector_mod.NumberSelector = _NumberSelector
    selector_mod.NumberSelectorMode = SimpleNamespace(BOX="box", SLIDER="slider")
    selector_mod.SelectSelectorConfig = _SelectSelectorConfig
    selector_mod.SelectSelector = _SelectSelector
    selector_mod.SelectSelectorMode = SimpleNamespace(LIST="list", DROPDOWN="dropdown")
    selector_mod.SelectOptionDict = _SelectOptionDict
    selector_mod.TextSelectorConfig = _TextSelectorConfig
    selector_mod.TextSelector = _TextSelector
    selector_mod.TextSelectorType = SimpleNamespace(PASSWORD="password")

    helpers_mod = types.ModuleType("homeassistant.helpers")
    aiohttp_mod = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_mod.async_get_clientsession = lambda hass: None

    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.config_entries", config_entries_mod)
    sys.modules.setdefault("homeassistant.core", core_mod)
    sys.modules.setdefault("homeassistant.data_entry_flow", data_entry_flow_mod)
    sys.modules.setdefault("homeassistant.helpers", helpers_mod)
    sys.modules.setdefault("homeassistant.helpers.selector", selector_mod)
    sys.modules.setdefault("homeassistant.helpers.aiohttp_client", aiohttp_mod)


# Set up the flow_power_ha package path before installing stubs, so that
# direct imports like `from flow_power_ha.const import ...` also work.
package = types.ModuleType("flow_power_ha")
package.__path__ = [str(COMPONENT_ROOT)]
sys.modules.setdefault("flow_power_ha", package)

_install_homeassistant_stubs()

from flow_power_ha.config_flow import FlowPowerSyncOptionsFlow  # noqa: E402
from flow_power_ha.const import CONF_EXPORT_SENSOR  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _schema_keys(schema: vol.Schema) -> set[str]:
    """Return the string key names present in a voluptuous Schema."""
    keys = set()
    for key in schema.schema:
        if isinstance(key, (vol.Required, vol.Optional)):
            keys.add(key.schema)
        elif isinstance(key, str):
            keys.add(key)
    return keys


def _build_options_flow(current: dict) -> FlowPowerSyncOptionsFlow:
    """Return an OptionsFlow instance with a minimal config_entry stub."""
    flow = object.__new__(FlowPowerSyncOptionsFlow)
    flow.__init__()
    flow.config_entry = SimpleNamespace(
        data=current,
        options={},
    )
    return flow


# ---------------------------------------------------------------------------
# 1. async_step_pricing schema contains the export_sensor field (Req 1.1, 1.2)
# ---------------------------------------------------------------------------

def test_async_step_pricing_schema_contains_export_sensor_field() -> None:
    """The pricing step schema must include CONF_EXPORT_SENSOR as an Optional field."""
    source = (COMPONENT_ROOT / "config_flow.py").read_text()

    # Verify the field is present in the pricing step form definition
    assert "CONF_EXPORT_SENSOR" in source
    assert "async_step_pricing" in source

    # More precisely: CONF_EXPORT_SENSOR must appear as a vol.Optional field in
    # async_step_pricing alongside an EntitySelector filtered to domain="sensor"
    assert 'vol.Optional(CONF_EXPORT_SENSOR)' in source
    assert 'EntitySelector' in source
    assert 'domain="sensor"' in source


def test_async_step_pricing_export_sensor_uses_entity_selector() -> None:
    """The export_sensor field must use an EntitySelector with domain='sensor'."""
    source = (COMPONENT_ROOT / "config_flow.py").read_text()

    # Confirm that EntitySelectorConfig with domain="sensor" appears in both
    # async_step_pricing (setup flow) and _init_schema (options flow).
    # The source may format the argument on a separate line, so search flexibly.
    assert "EntitySelectorConfig" in source

    # async_step_pricing body
    pricing_start = source.index("async def async_step_pricing")
    next_def = source.find("\n    async def ", pricing_start + 1)
    pricing_body = source[pricing_start: next_def if next_def != -1 else len(source)]
    assert "EntitySelector" in pricing_body, (
        "async_step_pricing must use EntitySelector for CONF_EXPORT_SENSOR"
    )
    assert 'domain="sensor"' in pricing_body, (
        "async_step_pricing EntitySelector must filter to domain='sensor'"
    )

    # _init_schema body
    init_start = source.index("def _init_schema")
    init_next = source.find("\n    async def ", init_start + 1)
    init_body = source[init_start: init_next if init_next != -1 else len(source)]
    assert "EntitySelector" in init_body, (
        "_init_schema must use EntitySelector for CONF_EXPORT_SENSOR"
    )
    assert 'domain="sensor"' in init_body, (
        "_init_schema EntitySelector must filter to domain='sensor'"
    )


# ---------------------------------------------------------------------------
# 2. _init_schema contains the export_sensor field (Req 1.1, 1.2)
# ---------------------------------------------------------------------------

def test_init_schema_contains_export_sensor_field() -> None:
    """_init_schema must include export_sensor as a vol.Optional key."""
    current = {
        "nem_region": "NSW1",
        "plan": "happy_hour",
        "base_rate": 34.0,
        "pea_enabled": True,
    }
    flow = _build_options_flow(current)
    schema = flow._init_schema(current)
    keys = _schema_keys(schema)

    assert CONF_EXPORT_SENSOR in keys, (
        f"'export_sensor' not found in _init_schema keys: {keys}"
    )


def test_init_schema_export_sensor_is_optional() -> None:
    """export_sensor must be vol.Optional (not vol.Required) in _init_schema."""
    current = {
        "nem_region": "NSW1",
        "plan": "happy_hour",
        "base_rate": 34.0,
        "pea_enabled": True,
    }
    flow = _build_options_flow(current)
    schema = flow._init_schema(current)

    optional_keys = {
        key.schema
        for key in schema.schema
        if isinstance(key, vol.Optional)
    }
    assert CONF_EXPORT_SENSOR in optional_keys, (
        f"'export_sensor' should be vol.Optional but found in: {optional_keys}"
    )


def test_init_schema_export_sensor_prepopulated_from_current() -> None:
    """_init_schema must pre-populate export_sensor with the current configured value."""
    entity_id = "sensor.solar_export"
    current = {
        "nem_region": "NSW1",
        "plan": "happy_hour",
        "base_rate": 34.0,
        "pea_enabled": True,
        CONF_EXPORT_SENSOR: entity_id,
    }
    flow = _build_options_flow(current)
    schema = flow._init_schema(current)

    # Find the Optional(CONF_EXPORT_SENSOR) key and check its suggested_value
    for key in schema.schema:
        if isinstance(key, vol.Optional) and key.schema == CONF_EXPORT_SENSOR:
            suggested = (key.description or {}).get("suggested_value")
            assert suggested == entity_id, (
                f"Expected suggested_value={entity_id!r}, got {suggested!r}"
            )
            break
    else:
        raise AssertionError("CONF_EXPORT_SENSOR key not found in schema")


def test_init_schema_export_sensor_suggested_value_is_none_when_not_configured() -> None:
    """When no export sensor is configured, suggested_value must be None."""
    current = {
        "nem_region": "NSW1",
        "plan": "happy_hour",
        "base_rate": 34.0,
        "pea_enabled": True,
    }
    flow = _build_options_flow(current)
    schema = flow._init_schema(current)

    for key in schema.schema:
        if isinstance(key, vol.Optional) and key.schema == CONF_EXPORT_SENSOR:
            suggested = (key.description or {}).get("suggested_value")
            assert suggested is None, (
                f"Expected suggested_value=None, got {suggested!r}"
            )
            break
    else:
        raise AssertionError("CONF_EXPORT_SENSOR key not found in schema")


# ---------------------------------------------------------------------------
# 3. Submitting without a sensor stores None (Req 1.3)
# ---------------------------------------------------------------------------

def test_init_schema_accepts_submission_without_export_sensor() -> None:
    """Submitting without an export_sensor value must not raise a validation error."""
    current = {
        "nem_region": "NSW1",
        "plan": "happy_hour",
        "base_rate": 34.0,
        "pea_enabled": True,
    }
    flow = _build_options_flow(current)
    schema = flow._init_schema(current)

    # A submission that omits export_sensor entirely must pass schema validation
    submission = {
        "plan": "happy_hour",
        "base_rate": 34.0,
        "pea_enabled": True,
    }
    # vol.Schema raises on invalid data; we expect no exception here
    try:
        validated = schema(submission)
    except vol.Invalid as exc:
        raise AssertionError(
            f"Schema rejected submission without export_sensor: {exc}"
        ) from exc

    # The key should either be absent or None — not a required value
    assert validated.get(CONF_EXPORT_SENSOR) is None


# ---------------------------------------------------------------------------
# 4. Submitting with an entity_id stores the string value (Req 1.4)
# ---------------------------------------------------------------------------

def test_init_schema_stores_entity_id_when_export_sensor_provided() -> None:
    """Submitting with a sensor entity_id must preserve the string value."""
    entity_id = "sensor.solar_export_total"
    current = {
        "nem_region": "NSW1",
        "plan": "happy_hour",
        "base_rate": 34.0,
        "pea_enabled": True,
    }
    flow = _build_options_flow(current)
    schema = flow._init_schema(current)

    submission = {
        "plan": "happy_hour",
        "base_rate": 34.0,
        "pea_enabled": True,
        CONF_EXPORT_SENSOR: entity_id,
    }
    validated = schema(submission)

    assert validated[CONF_EXPORT_SENSOR] == entity_id


# ---------------------------------------------------------------------------
# 5. Field is shown for all plans — no plan-conditional gating (Req 1.2)
# ---------------------------------------------------------------------------

def test_init_schema_includes_export_sensor_for_all_plans() -> None:
    """export_sensor must appear in _init_schema regardless of the configured plan."""
    from flow_power_ha.const import (
        PLAN_FLOW_HOME,
        PLAN_HAPPY_HOUR,
        PLAN_4FREE,
        PLAN_LEGACY_HAPPY_HOUR,
    )

    for plan in (PLAN_FLOW_HOME, PLAN_HAPPY_HOUR, PLAN_4FREE, PLAN_LEGACY_HAPPY_HOUR):
        current = {
            "nem_region": "NSW1",
            "plan": plan,
            "base_rate": 34.0,
            "pea_enabled": True,
        }
        flow = _build_options_flow(current)
        schema = flow._init_schema(current)
        keys = _schema_keys(schema)

        assert CONF_EXPORT_SENSOR in keys, (
            f"'export_sensor' missing from _init_schema for plan={plan!r}"
        )


# ---------------------------------------------------------------------------
# 6. Config flow does not validate sensor availability at config time (Req 1.5)
# ---------------------------------------------------------------------------

def test_async_step_pricing_does_not_validate_sensor_availability() -> None:
    """The config flow must not contain any HA state validation for the sensor."""
    source = (COMPONENT_ROOT / "config_flow.py").read_text()

    # There must be no call to hass.states.get() in the pricing step or
    # anywhere that would gate the export_sensor field on sensor availability
    # (that logic belongs only in the coordinator).
    assert "async_step_pricing" in source

    # The pricing step should not call hass.states.get
    # Extract only the async_step_pricing function body for inspection
    pricing_start = source.index("async def async_step_pricing")
    # Find the next top-level async def after async_step_pricing
    next_def = source.find("\n    async def ", pricing_start + 1)
    if next_def == -1:
        next_def = len(source)
    pricing_body = source[pricing_start:next_def]

    assert "hass.states.get" not in pricing_body, (
        "async_step_pricing must not validate sensor state — "
        "that belongs in the coordinator"
    )
