from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components" / "flow_power_ha"

package = types.ModuleType("flow_power_ha")
package.__path__ = [str(COMPONENT_ROOT)]
sys.modules.setdefault("flow_power_ha", package)

from flow_power_ha.pricing import calculate_forecast_prices, calculate_import_price  # noqa: E402
from flow_power_ha.tariff_utils import _dispatch_interval_end  # noqa: E402
from flow_power_ha.flow_power_api import FlowPowerAPIClient, FlowPowerAPIError  # noqa: E402


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
    assert "predispatch30mins(" in coordinator_source
    assert "predispatch5mins(" in coordinator_source
    assert "get_residential_site_summary" in coordinator_source
    assert "CONF_FLOWPOWER_API_KEY" in sensor_source
    assert "CONF_FLOWPOWER_NMI" in sensor_source
