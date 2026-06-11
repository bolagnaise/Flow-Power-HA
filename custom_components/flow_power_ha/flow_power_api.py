"""Flow Power KWatch API client."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from math import isfinite
from typing import Any

import aiohttp

from .const import FLOWPOWER_API_BASE_URL

_LOGGER = logging.getLogger(__name__)


class FlowPowerAPIError(Exception):
    """Raised when the Flow Power API returns an unusable response."""


class FlowPowerAPIClient:
    """Client for Flow Power's KWatch API."""

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client."""
        self._api_key = api_key
        self._session = session
        self._owns_session = session is None

    async def close(self) -> None:
        """Close the owned HTTP session."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return an open client session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def _post(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """POST to a KWatch endpoint and return decoded JSON."""
        session = await self._get_session()
        url = f"{FLOWPOWER_API_BASE_URL}/{endpoint}"
        request_kwargs: dict[str, Any] = {
            "headers": {
                "x-api-key": self._api_key,
                "Accept": "application/json",
            },
            "timeout": aiohttp.ClientTimeout(total=30),
        }
        if payload is not None:
            request_kwargs["json"] = payload

        async with session.post(url, **request_kwargs) as resp:
            text = await resp.text()
            if resp.status in (401, 403):
                raise FlowPowerAPIError("invalid_api_key")
            if resp.status >= 400:
                raise FlowPowerAPIError(f"api_status_{resp.status}")
            try:
                data = await resp.json(content_type=None)
            except Exception as err:
                _LOGGER.debug(
                    "Flow Power API %s returned non-JSON response: %s",
                    endpoint,
                    text[:200],
                )
                raise FlowPowerAPIError("invalid_json") from err
            return self._decode_nested_json(data, endpoint)

    @staticmethod
    def _decode_nested_json(payload: Any, endpoint: str) -> Any:
        """Decode KWatch responses that wrap JSON inside a JSON string."""
        decoded = payload
        for _ in range(3):
            if not isinstance(decoded, str):
                return decoded
            text = decoded.strip()
            if not text:
                return decoded
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                return decoded
            _LOGGER.debug("Flow Power API %s returned nested JSON string", endpoint)
        return decoded

    @staticmethod
    def _records(payload: Any, *keys: str) -> list[dict[str, Any]]:
        """Return dict records from common API wrapper shapes."""
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in keys + ("data", "result", "results", "items", "value"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    nested = FlowPowerAPIClient._records(value, *keys)
                    if nested:
                        return nested
            return [payload]
        return []

    @staticmethod
    def _first_number(record: dict[str, Any], *keys: str) -> float | None:
        """Return the first finite numeric value for one of the supplied keys."""
        for key in keys:
            value = record.get(key)
            if value in (None, ""):
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if isfinite(parsed):
                return parsed
        return None

    @staticmethod
    def _first_text(record: dict[str, Any], *keys: str) -> str | None:
        """Return the first non-empty string value for one of the supplied keys."""
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        """Parse common KWatch timestamp formats."""
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value

        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        for fmt in (
            None,
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
        ):
            try:
                if fmt is None:
                    return datetime.fromisoformat(text)
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    async def get_residential_sites(self) -> list[dict[str, Any]]:
        """Return residential sites available to the API key."""
        payload = await self._post("GetResidentialSites")
        sites = self._records(payload, "sites")
        normalized = []
        for site in sites:
            nmi = self._first_text(site, "nmi", "NMI", "Nmi")
            if not nmi:
                continue
            normalized.append(
                {
                    "nmi": nmi,
                    "networkTariff": self._first_text(
                        site,
                        "networkTariff",
                        "NetworkTariff",
                        "network_tariff",
                    ),
                    "raw": site,
                }
            )
        return normalized

    async def get_residential_site(self, nmi: str) -> dict[str, Any] | None:
        """Return one residential site."""
        payload = await self._post("GetResidentialSite", {"nmi": nmi})
        records = self._records(payload, "site", "sites")
        return records[0] if records else None

    async def get_residential_site_summary(self, nmi: str) -> dict[str, Any] | None:
        """Return normalized account summary values for one NMI."""
        payload = await self._post("GetResidentialSiteSummary", {"nmi": nmi})
        records = self._records(payload, "summary")
        if not records:
            return None
        return normalize_site_summary(records[0])

    async def dispatch5mins(
        self, reg_name: str, period: float = 60
    ) -> list[dict[str, Any]]:
        """Return 5-minute dispatch prices in $/MWh."""
        payload = await self._post(
            "dispatch5mins",
            {"regName": reg_name, "period": period},
        )
        return self._normalize_price_records(payload, duration=5)

    async def predispatch5mins(
        self, reg_name: str, period: float = 60
    ) -> list[dict[str, Any]]:
        """Return 5-minute predispatch prices in $/MWh."""
        payload = await self._post(
            "predispatch5mins",
            {"regName": reg_name, "period": period},
        )
        return self._normalize_price_records(payload, duration=5)

    async def predispatch30mins(
        self, reg_name: str, period: float = 2
    ) -> list[dict[str, Any]]:
        """Return 30-minute predispatch prices in $/MWh."""
        payload = await self._post(
            "predispatch30mins",
            {"regName": reg_name, "period": period},
        )
        return self._normalize_price_records(payload, duration=30)

    def _normalize_price_records(
        self,
        payload: Any,
        *,
        duration: int,
    ) -> list[dict[str, Any]]:
        """Normalize KWatch price records to existing forecast input shape."""
        records = self._records(
            payload,
            "prices",
            "dispatch",
            "predispatch",
            "priceData",
            "PriceData",
        )
        normalized: list[dict[str, Any]] = []
        for record in records:
            price_mwh = self._first_number(
                record,
                "price",
                "Price",
                "rrp",
                "RRP",
                "Rrp",
                "value",
                "Value",
                "dispatchPrice",
                "DispatchPrice",
            )
            if price_mwh is None:
                continue
            period_time = self._parse_time(
                self._first_text(
                    record,
                    "time",
                    "Time",
                    "timestamp",
                    "Timestamp",
                    "settlementDate",
                    "SettlementDate",
                    "dateTime",
                    "DateTime",
                    "periodDateTime",
                    "PeriodDateTime",
                )
            )
            timestamp = (
                period_time.isoformat()
                if period_time is not None
                else datetime.now().isoformat()
            )
            normalized.append(
                {
                    "nemTime": timestamp,
                    "perKwh": price_mwh / 10.0,
                    "wholesaleKWHPrice": price_mwh / 1000.0,
                    "price_mwh": price_mwh,
                    "duration": duration,
                    "raw": record,
                }
            )

        normalized.sort(key=lambda item: item["nemTime"])
        return normalized


def normalize_site_summary(user_obj: dict[str, Any]) -> dict[str, Any]:
    """Normalize KWatch residential summary fields to the account sensor shape."""
    def number(key: str) -> float | None:
        value = user_obj.get(key)
        if value in (None, ""):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if isfinite(parsed) else None

    lwap = number("LWAP")
    twap = number("TWAP")
    lwap_imp = number("LWAPImp")
    twap_imp = number("TWAPImp")
    return {
        "lwap": lwap,
        "lwap_import": lwap_imp,
        "lwap_actual": number("LWAPActual"),
        "lwap_import_actual": number("LWAPImpActual"),
        "twap": twap,
        "twap_import": twap_imp,
        "avg_rrp": number("AvgRRP"),
        "avg_usage_kw": number("AvgUsage"),
        "avg_import_usage_kw": number("AvgImpUsage"),
        "max_usage_kw": number("MaxUsage"),
        "total_intervals": number("TotalInterval"),
        "pea_30_days": number("PEA30Days"),
        "pea_30_import": number("PEA30ImportDays"),
        "pea_actual": number("PEAActual"),
        "pea_target": number("PEATarget"),
        "pea_actual_import": number("PEAActualImport"),
        "pea_target_import": number("PEATargetImport"),
        "bpea": number("PEATarget"),
        "bpea_import": number("PEATargetImport"),
        "cpea": (lwap or 0) - (twap or 0),
        "cpea_import": (lwap_imp or 0) - (twap_imp or 0),
        "site_losses_dlf": number("SiteLosses"),
        "gst_multiplier": number("GST"),
        "source": "api",
    }
