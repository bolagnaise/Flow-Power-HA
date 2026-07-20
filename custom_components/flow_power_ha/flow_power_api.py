"""Flow Power KWatch API client."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any

import aiohttp

from .const import FLOWPOWER_API_BASE_URL

_LOGGER = logging.getLogger(__name__)


class FlowPowerAPIError(Exception):
    """Raised when the Flow Power API returns an unusable response."""


async def probe_api_access(
    client: FlowPowerAPIClient,
    reg_name: str,
) -> dict[str, Any]:
    """Validate API access without requiring every optional endpoint to work."""
    site_lookup_error: str | None = None
    try:
        sites = await client.get_residential_sites()
    except FlowPowerAPIError as err:
        if str(err) == "invalid_api_key":
            return {"success": False, "error": "invalid_api_key"}
        site_lookup_error = str(err)
        sites = []
    except aiohttp.ClientError:
        site_lookup_error = "cannot_connect"
        sites = []
    except Exception as err:
        _LOGGER.exception("Flow Power API site validation failed: %s", err)
        site_lookup_error = "cannot_connect"
        sites = []

    if sites:
        return {"success": True, "sites": sites}

    # Current dispatch pricing is sufficient for a working price-only entry.
    # Forecast endpoints are optional at runtime and must not reject a valid key.
    try:
        dispatch = await client.dispatch5mins(reg_name, period=60)
    except FlowPowerAPIError as err:
        if str(err) == "invalid_api_key":
            return {"success": False, "error": "invalid_api_key"}
        return {"success": False, "error": "cannot_connect"}
    except aiohttp.ClientError:
        return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.exception("Flow Power API price validation failed: %s", err)
        return {"success": False, "error": "cannot_connect"}

    if dispatch:
        return {
            "success": True,
            "sites": [],
            "site_lookup_error": site_lookup_error or "no_sites",
        }
    return {
        "success": False,
        "error": "cannot_connect" if site_lookup_error else "no_sites",
    }


async def probe_residential_nmi(
    client: FlowPowerAPIClient,
    nmi: str,
) -> dict[str, Any]:
    """Validate that an NMI exposes residential account summary data."""
    try:
        summary = await client.get_residential_site_summary(nmi)
    except FlowPowerAPIError as err:
        if str(err) == "invalid_api_key":
            return {"success": False, "error": "invalid_api_key"}
        if str(err) in ("api_status_400", "api_status_404"):
            return {"success": False, "error": "invalid_site"}
        return {"success": False, "error": "cannot_connect"}
    except aiohttp.ClientError:
        return {"success": False, "error": "cannot_connect"}
    except Exception as err:
        _LOGGER.exception("Flow Power API NMI validation failed: %s", err)
        return {"success": False, "error": "cannot_connect"}

    summary_fields = (
        "lwap",
        "twap",
        "lwap_import",
        "twap_import",
        "avg_rrp",
        "pea_actual",
        "pea_target",
        "total_intervals",
        "site_losses_dlf",
        "gst_multiplier",
    )
    if summary and any(summary.get(field) is not None for field in summary_fields):
        return {"success": True, "summary": summary}
    return {"success": False, "error": "invalid_site"}


def merge_price_forecasts(
    *forecast_sets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge forecast arrays by timestamp, preferring finer-grained records."""
    merged: dict[str, dict[str, Any]] = {}

    for forecast_set in forecast_sets:
        for record in forecast_set:
            timestamp = record.get("nemTime") or record.get("startTime")
            if not isinstance(timestamp, str) or not timestamp:
                continue

            existing = merged.get(timestamp)
            if existing is None:
                merged[timestamp] = record
                continue

            existing_duration = int(existing.get("duration", 30) or 30)
            candidate_duration = int(record.get("duration", 30) or 30)
            if candidate_duration < existing_duration:
                merged[timestamp] = record

    return sorted(
        merged.values(),
        key=lambda item: item.get("nemTime") or item.get("startTime") or "",
    )


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
                value = FlowPowerAPIClient._get_value(payload, key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    nested = FlowPowerAPIClient._records(value, *keys)
                    if nested:
                        return nested
            return [payload]
        return []

    @staticmethod
    def _mapping_price_records(payload: Any) -> list[dict[str, Any]]:
        """Extract timestamp->price mappings from nested endpoint payloads."""
        if isinstance(payload, list):
            for item in payload:
                records = FlowPowerAPIClient._mapping_price_records(item)
                if records:
                    return records
            return []

        if not isinstance(payload, dict):
            return []

        records: list[dict[str, Any]] = []
        for key, value in payload.items():
            timestamp = FlowPowerAPIClient._parse_time(key)
            if timestamp is None:
                continue

            price = None
            if isinstance(value, dict):
                price = FlowPowerAPIClient._first_number(
                    value,
                    "price",
                    "Price",
                    "priceMwh",
                    "price_mwh",
                    "rrp",
                    "RRP",
                    "Rrp",
                    "value",
                    "Value",
                    "dispatchPrice",
                    "DispatchPrice",
                )
            else:
                try:
                    price = float(value)
                except (TypeError, ValueError):
                    price = None

            if price is None or not isfinite(price):
                continue

            records.append({"key": key, "price": price, "raw": value})

        if records:
            records.sort(
                key=lambda item: (
                    FlowPowerAPIClient._parse_time(item["key"])
                    or datetime.min.replace(tzinfo=timezone.utc)
                )
            )
            return records

        for value in payload.values():
            records = FlowPowerAPIClient._mapping_price_records(value)
            if records:
                return records
        return []

    @staticmethod
    def _normalize_key(key: str) -> str:
        """Normalize API keys for case/underscore-insensitive lookup."""
        return "".join(ch for ch in key.lower() if ch.isalnum())

    @staticmethod
    def _get_value(record: dict[str, Any], key: str) -> Any:
        """Return an exact or normalized key match from a record."""
        if key in record:
            return record[key]

        wanted = FlowPowerAPIClient._normalize_key(key)
        for record_key, value in record.items():
            if FlowPowerAPIClient._normalize_key(str(record_key)) == wanted:
                return value
        return None

    @staticmethod
    def _first_number(record: dict[str, Any], *keys: str) -> float | None:
        """Return the first finite numeric value for one of the supplied keys."""
        for key in keys:
            value = FlowPowerAPIClient._get_value(record, key)
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
            value = FlowPowerAPIClient._get_value(record, key)
            if value not in (None, ""):
                return str(value)
        return None

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        """Parse common KWatch timestamp formats."""
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

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
            "%Y/%m/%d %H:%M",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
        ):
            try:
                if fmt is None:
                    parsed = datetime.fromisoformat(text)
                else:
                    parsed = datetime.strptime(text, fmt)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    @staticmethod
    def _align_to_interval(value: datetime, duration: int) -> datetime:
        """Snap a timestamp down to the start of its interval."""
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        minute = (value.minute // duration) * duration
        return value.replace(minute=minute, second=0, microsecond=0)

    async def get_residential_sites(self) -> list[dict[str, Any]]:
        """Return residential sites available to the API key."""
        # The API documents this as an empty JSON request body, not a bodyless
        # POST. Some accounts reject or return no metadata without the object.
        payload = await self._post("GetResidentialSites", {})
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
        fallback_start = self._align_to_interval(datetime.now(timezone.utc), duration)
        for idx, record in enumerate(records):
            explicit_time = self._parse_time(
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
                    "intervalDateTime",
                    "IntervalDateTime",
                    "forecastDateTime",
                    "ForecastDateTime",
                    "tradingInterval",
                    "TradingInterval",
                    "tradingIntervalStart",
                    "TradingIntervalStart",
                    "startTime",
                    "StartTime",
                    "key",
                    "Key",
                )
            )
            if explicit_time is not None:
                fallback_start = explicit_time - timedelta(minutes=duration * idx)
                break
        inferred_timestamps = 0
        for idx, record in enumerate(records):
            price_mwh = self._first_number(
                record,
                "price",
                "Price",
                "priceMwh",
                "price_mwh",
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
                    "intervalDateTime",
                    "IntervalDateTime",
                    "forecastDateTime",
                    "ForecastDateTime",
                    "tradingInterval",
                    "TradingInterval",
                    "tradingIntervalStart",
                    "TradingIntervalStart",
                    "startTime",
                    "StartTime",
                    "key",
                    "Key",
                )
            )
            if period_time is None:
                period_time = fallback_start + timedelta(minutes=duration * idx)
                inferred_timestamps += 1
            normalized.append(
                {
                    "nemTime": period_time.isoformat(),
                    "perKwh": price_mwh / 10.0,
                    "wholesaleKWHPrice": price_mwh / 1000.0,
                    "price_mwh": price_mwh,
                    "duration": duration,
                    "raw": record,
                }
            )

        if inferred_timestamps:
            _LOGGER.debug(
                "Flow Power API inferred %d/%d price timestamps from response order",
                inferred_timestamps,
                len(normalized),
            )
        if not normalized:
            mapped_records = self._mapping_price_records(payload)
            if mapped_records and mapped_records != records:
                return self._normalize_price_records(mapped_records, duration=duration)
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
