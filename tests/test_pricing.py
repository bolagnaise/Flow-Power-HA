from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components" / "flow_power_ha"

package = types.ModuleType("flow_power_ha")
package.__path__ = [str(COMPONENT_ROOT)]
sys.modules.setdefault("flow_power_ha", package)

from flow_power_ha.pricing import (  # noqa: E402
    calculate_export_price,
    calculate_forecast_prices,
    calculate_import_price,
)
from flow_power_ha.flow_power_pricing import (  # noqa: E402
    calculate_flow_power_pea,
    resolve_flow_power_pricing_context,
)
from flow_power_ha.tariff_utils import _dispatch_interval_end  # noqa: E402
from flow_power_ha.flow_power_api import (  # noqa: E402
    FlowPowerAPIClient,
    FlowPowerAPIError,
    merge_price_forecasts,
)


class _FakeResponse:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def text(self) -> str:
        return json.dumps(self._payload)

    async def json(self, content_type=None):
        return self._payload


class _FakeSession:
    closed = False

    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict[str, object] | None, dict[str, str]]] = []

    def post(self, url: str, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append(
            (
                endpoint,
                kwargs.get("json"),
                kwargs.get("headers", {}),
            )
        )
        payload = self.payloads[endpoint]
        if isinstance(payload, tuple):
            payload, status = payload
        else:
            status = 200
        return _FakeResponse(payload, status)


def test_import_price_exposes_network_tou_adjustment() -> None:
    price = calculate_import_price(
        wholesale_cents=20.0,
        base_rate=34.0,
        twap=10.0,
        network_tariff_rate=5.6,
        avg_daily_tariff=1.1,
    )

    assert price["final_cents"] == 47.8
    assert price["network_tou_adjustment"] == 4.5
    assert price["price_without_network_tou_adjustment_cents"] == 43.3
    assert price["price_without_network_tou_adjustment_dollars"] == 0.433


def test_pricing_context_uses_account_import_values() -> None:
    context = resolve_flow_power_pricing_context(
        options={},
        data={},
        domain_data={
            "flow_power_twap_tracker": SimpleNamespace(twap=8.25),
            "flow_power_portal_data": {
                "twap": 21.0,
                "twap_import": 20.5,
                "bpea": 2.3,
                "bpea_import": 2.1,
                "gst_multiplier": 1.2,
            },
        },
    )

    assert context.twap == 20.5
    assert context.twap_source == "portal"
    assert context.bpea == 2.1
    assert context.bpea_source == "portal"
    assert context.gst_multiplier == 1.2
    assert round(
        calculate_flow_power_pea(
            20.0,
            context,
            tariff_rate=12.0,
            avg_daily_tariff=5.0,
        ),
        2,
    ) == 4.3


def test_import_price_prefers_portal_account_twap_over_tracker() -> None:
    price = calculate_import_price(
        wholesale_cents=20.0,
        base_rate=34.0,
        twap=8.25,
        network_tariff_rate=12.0,
        avg_daily_tariff=5.0,
        pricing_context=resolve_flow_power_pricing_context(
            options={},
            data={},
            domain_data={
                "flow_power_twap_tracker": SimpleNamespace(twap=8.25),
                "flow_power_portal_data": {
                    "twap": 21.0,
                    "twap_import": 20.5,
                    "bpea": 2.3,
                    "bpea_import": 2.1,
                    "gst_multiplier": 1.2,
                },
            },
        ),
    )

    assert price["twap_used"] == 20.5
    assert price["twap_source"] == "portal"
    assert price["bpea"] == 2.1
    assert price["gst_multiplier"] == 1.2
    assert round(price["pea"], 2) == 4.3
    assert round(price["final_cents"], 2) == 38.3


def test_pricing_context_uses_override_before_account_twap() -> None:
    context = resolve_flow_power_pricing_context(
        options={"fp_twap_override": 12.34},
        data={},
        domain_data={
            "flow_power_twap_tracker": SimpleNamespace(twap=8.25),
            "flow_power_portal_data": {
                "twap_import": 20.5,
                "bpea_import": 2.1,
                "gst_multiplier": 1.1,
            },
        },
    )

    assert context.twap == 12.34
    assert context.twap_source == "override"
    assert context.bpea == 2.1


def test_import_price_uses_portal_aware_pricing_context() -> None:
    context = resolve_flow_power_pricing_context(
        options={},
        data={},
        domain_data={
            "flow_power_twap_tracker": SimpleNamespace(twap=11.49),
            "flow_power_portal_data": {
                "twap": 21.02,
                "twap_import": 21.02,
                "bpea_import": 1.7,
                "gst_multiplier": 1.1,
            },
        },
    )

    price = calculate_import_price(
        wholesale_cents=11.02,
        base_rate=34.0,
        network_tariff_rate=5.85,
        avg_daily_tariff=10.48,
        pricing_context=context,
    )

    assert price["twap_used"] == 21.02
    assert price["twap_source"] == "portal"
    assert price["bpea"] == 1.7
    assert price["bpea_source"] == "portal"
    assert price["gst_multiplier"] == 1.1
    assert round(price["pea"], 2) == -17.33
    assert price["final_cents"] == 16.67


def test_pricing_context_falls_back_to_general_bpea_when_import_bpea_is_zero() -> None:
    context = resolve_flow_power_pricing_context(
        options={},
        data={},
        domain_data={
            "flow_power_twap_tracker": SimpleNamespace(twap=11.49),
            "flow_power_portal_data": {
                "twap": 18.56,
                "twap_import": 18.56,
                "bpea": 2.057245,
                "bpea_import": 0.0,
                "gst_multiplier": 1.1,
            },
        },
    )

    price = calculate_import_price(
        wholesale_cents=7.527,
        base_rate=34.0,
        network_tariff_rate=5.1535,
        avg_daily_tariff=11.1764,
        pricing_context=context,
    )

    assert context.bpea == 2.057245
    assert context.bpea_source == "portal"
    assert price["bpea"] == 2.057245
    assert round(price["pea"], 2) == -20.22
    assert price["final_cents"] == 13.78


def test_price_without_network_tou_adjustment_preserves_account_inputs() -> None:
    context = resolve_flow_power_pricing_context(
        options={},
        data={},
        domain_data={
            "flow_power_twap_tracker": SimpleNamespace(twap=11.49),
            "flow_power_portal_data": {
                "twap": 18.56,
                "twap_import": 18.56,
                "bpea": 2.057245,
                "bpea_import": 2.057245,
                "gst_multiplier": 1.1,
            },
        },
    )

    price = calculate_import_price(
        wholesale_cents=7.527,
        base_rate=34.0,
        network_tariff_rate=5.1535,
        avg_daily_tariff=11.1764,
        pricing_context=context,
    )

    assert price["network_tou_adjustment"] == -6.0229
    assert price["price_without_network_tou_adjustment_cents"] == 19.81
    assert price["final_cents"] == 13.78
    assert round(
        price["price_without_network_tou_adjustment_cents"]
        - price["final_cents"],
        4,
    ) == 6.03
    assert price["twap_used"] == 18.56
    assert price["bpea"] == 2.057245
    assert price["gst_multiplier"] == 1.1


def test_dispatch_interval_end_uses_next_five_minute_boundary() -> None:
    assert _dispatch_interval_end(
        datetime(2026, 5, 27, 10, 0, 5, 123456, tzinfo=timezone.utc)
    ) == datetime(2026, 5, 27, 10, 5, tzinfo=timezone.utc)

    assert _dispatch_interval_end(
        datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 5, 27, 10, 5, tzinfo=timezone.utc)

    assert _dispatch_interval_end(
        datetime(2026, 5, 27, 9, 59, 59, 999999, tzinfo=timezone.utc)
    ) == datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)


def test_flowpower_api_client_normalizes_sites_summary_and_prices() -> None:
    session = _FakeSession(
        {
            "GetResidentialSites": {
                "sites": [{"nmi": "4407000000", "networkTariff": "BLNREX2"}]
            },
            "GetResidentialSiteSummary": {
                "LWAP": "16.3",
                "TWAP": "18.7",
                "LWAPImp": 23.6,
                "TWAPImp": 18.7,
                "PEATarget": 0.0,
                "PEATargetImport": 0.0,
                "PEAActual": "-2.4",
                "GST": "1.1",
            },
            "dispatch5mins": {
                "data": [{"timestamp": "2026-06-08T10:00:00+10:00", "price": 123.4}]
            },
            "predispatch30mins": {
                "result": [{"periodDateTime": "2026/06/08 10:30:00", "RRP": 98.0}]
            },
        }
    )
    client = FlowPowerAPIClient("secret-key", session)  # type: ignore[arg-type]

    async def run():
        sites = await client.get_residential_sites()
        summary = await client.get_residential_site_summary("4407000000")
        dispatch = await client.dispatch5mins("nsw")
        forecast = await client.predispatch30mins("nsw")
        return sites, summary, dispatch, forecast

    sites, summary, dispatch, forecast = asyncio.run(run())

    assert sites[0]["nmi"] == "4407000000"
    assert sites[0]["networkTariff"] == "BLNREX2"
    assert summary["source"] == "api"
    assert summary["pea_actual"] == -2.4
    assert summary["bpea"] == 0.0
    assert dispatch[0]["perKwh"] == 12.34
    assert forecast[0]["perKwh"] == 9.8
    assert forecast[0]["duration"] == 30
    assert all(call[2]["x-api-key"] == "secret-key" for call in session.calls)


def test_flowpower_api_client_decodes_nested_json_strings() -> None:
    session = _FakeSession(
        {
            "GetResidentialSites": json.dumps(
                {"sites": [{"nmi": "4407000000", "networkTariff": "BLNREX2"}]}
            ),
            "dispatch5mins": json.dumps(
                {"data": [{"timestamp": "2026-06-08T10:00:00+10:00", "price": 123.4}]}
            ),
        }
    )
    client = FlowPowerAPIClient("secret-key", session)  # type: ignore[arg-type]

    async def run():
        sites = await client.get_residential_sites()
        dispatch = await client.dispatch5mins("nsw")
        return sites, dispatch

    sites, dispatch = asyncio.run(run())

    assert sites[0]["nmi"] == "4407000000"
    assert dispatch[0]["perKwh"] == 12.34


def test_flowpower_api_client_normalizes_uppercase_fields_and_infers_timestamps() -> None:
    session = _FakeSession(
        {
            "predispatch30mins": {
                "RESULT": [
                    {"FORECAST_DATETIME": "2026/06/08 10:30", "PRICE": 100.0},
                    {"RRP": 200.0},
                    {"price_mwh": 300.0},
                ]
            }
        }
    )
    client = FlowPowerAPIClient("secret-key", session)  # type: ignore[arg-type]

    forecast = asyncio.run(client.predispatch30mins("nsw"))

    assert [entry["nemTime"] for entry in forecast] == [
        "2026-06-08T10:30:00+00:00",
        "2026-06-08T11:00:00+00:00",
        "2026-06-08T11:30:00+00:00",
    ]
    assert [entry["perKwh"] for entry in forecast] == [10.0, 20.0, 30.0]


def test_flowpower_price_endpoints_can_work_when_site_lookup_fails() -> None:
    session = _FakeSession(
        {
            "GetResidentialSites": ({"error": "site lookup unavailable"}, 500),
            "dispatch5mins": {
                "data": [{"timestamp": "2026-06-08T10:00:00+10:00", "price": 123.4}]
            },
            "predispatch30mins": {
                "result": [{"periodDateTime": "2026/06/08 10:30:00", "RRP": 98.0}]
            },
        }
    )
    client = FlowPowerAPIClient("secret-key", session)  # type: ignore[arg-type]

    async def run():
        try:
            await client.get_residential_sites()
        except FlowPowerAPIError as err:
            site_error = str(err)
        else:
            site_error = None
        dispatch = await client.dispatch5mins("nsw", period=1)
        forecast = await client.predispatch30mins("nsw", period=1)
        return site_error, dispatch, forecast

    site_error, dispatch, forecast = asyncio.run(run())

    assert site_error == "api_status_500"
    assert dispatch[0]["perKwh"] == 12.34
    assert forecast[0]["perKwh"] == 9.8
    assert [call[0] for call in session.calls] == [
        "GetResidentialSites",
        "dispatch5mins",
        "predispatch30mins",
    ]


def test_merge_price_forecasts_keeps_near_5_minute_slots() -> None:
    merged = merge_price_forecasts(
        [
            {"nemTime": "2026-06-26T06:45:00+10:00", "perKwh": 10.0, "duration": 5},
            {"nemTime": "2026-06-26T06:50:00+10:00", "perKwh": 11.0, "duration": 5},
            {"nemTime": "2026-06-26T06:55:00+10:00", "perKwh": 12.0, "duration": 5},
            {"nemTime": "2026-06-26T07:30:00+10:00", "perKwh": 13.0, "duration": 5},
        ],
        [
            {"nemTime": "2026-06-26T07:30:00+10:00", "perKwh": 99.0, "duration": 30},
            {"nemTime": "2026-06-26T08:00:00+10:00", "perKwh": 88.0, "duration": 30},
        ],
    )

    assert [entry["nemTime"] for entry in merged] == [
        "2026-06-26T06:45:00+10:00",
        "2026-06-26T06:50:00+10:00",
        "2026-06-26T06:55:00+10:00",
        "2026-06-26T07:30:00+10:00",
        "2026-06-26T08:00:00+10:00",
    ]
    assert merged[3]["perKwh"] == 13.0
    assert merged[3]["duration"] == 5


def test_flowpower_api_client_reads_timestamp_price_mappings() -> None:
    session = _FakeSession(
        {
            "predispatch5mins": {
                "data": {
                    "2026-06-23T06:45:00+10:00": 100.0,
                    "2026-06-23T06:50:00+10:00": 110.0,
                    "2026-06-23T06:55:00+10:00": 120.0,
                }
            }
        }
    )
    client = FlowPowerAPIClient("secret-key", session)  # type: ignore[arg-type]

    forecast = asyncio.run(client.predispatch5mins("nsw", period=60))

    assert [entry["nemTime"] for entry in forecast] == [
        "2026-06-23T06:45:00+10:00",
        "2026-06-23T06:50:00+10:00",
        "2026-06-23T06:55:00+10:00",
    ]
    assert [entry["perKwh"] for entry in forecast] == [10.0, 11.0, 12.0]
    assert all(entry["duration"] == 5 for entry in forecast)


def test_forecast_tariff_lookup_uses_interval_start_slot() -> None:
    forecast = calculate_forecast_prices(
        [
            {"nemTime": "2026-06-08T10:00:00+10:00", "perKwh": 10.0, "duration": 30},
            {"nemTime": "2026-06-08T10:30:00+10:00", "perKwh": 10.0, "duration": 30},
        ],
        base_rate=34.0,
        pea_enabled=True,
        twap=10.0,
        tariff_schedule={
            19: 1.5,  # 09:30-10:00
            20: 9.0,  # 10:00-10:30
        },
        avg_daily_tariff=0.0,
    )

    assert forecast[0]["network_tariff_rate"] == 1.5
    assert forecast[1]["network_tariff_rate"] == 9.0


def test_export_price_supports_happy_hour_override() -> None:
    price = calculate_export_price(
        "QLD1",
        current_time=datetime.fromisoformat("2026-06-16T18:00:00+10:00"),
        happy_hour_rate_override=0.5,
    )

    assert price["happy_hour_rate"] == 0.5
    assert price["export_cents"] == 50.0
    assert price["export_dollars"] == 0.5


def test_config_flow_and_coordinator_wire_kwatch_api_paths() -> None:
    config_flow_source = (COMPONENT_ROOT / "config_flow.py").read_text()
    coordinator_source = (COMPONENT_ROOT / "coordinator.py").read_text()
    sensor_source = (COMPONENT_ROOT / "sensor.py").read_text()

    assert "validate_flowpower_api_key" in config_flow_source
    assert "client.dispatch5mins(api_region, period=60)" in config_flow_source
    assert "client.predispatch30mins(api_region, period=1)" in config_flow_source
    assert "client.predispatch5mins(api_region, period=60)" in config_flow_source
    assert "if dispatch:" in config_flow_source
    assert "async_step_flowpower_site" in config_flow_source
    assert "async_step_flowpower_site_options" in config_flow_source
    assert "FlowPowerAPIClient(self.fp_api_key" in coordinator_source
    assert "dispatch5mins(api_region, period=60)" in coordinator_source
    assert "predispatch30mins(\n                api_region,\n                period=1," in coordinator_source
    assert "predispatch5mins(" in coordinator_source
    assert "merge_price_forecasts(forecast_5, forecast_30)" in coordinator_source
    assert "get_residential_site_summary" in coordinator_source
    assert "resolve_flow_power_pricing_context" in coordinator_source
    assert "pricing_context=pricing_context" in coordinator_source
    assert "def _publish_manual_data_update" in coordinator_source
    assert "self.async_update_listeners()" in coordinator_source
    assert "self._publish_manual_data_update(data)" in coordinator_source
    assert "CONF_FLOWPOWER_API_KEY" in sensor_source
    assert "CONF_FLOWPOWER_NMI" in sensor_source
