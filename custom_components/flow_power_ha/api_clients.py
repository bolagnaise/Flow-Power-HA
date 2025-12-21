"""API clients for AEMO and Amber price data."""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import zipfile
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from .const import (
    AEMO_5MIN_PREDISPATCH_URL,
    AEMO_CURRENT_PRICE_URL,
    AEMO_FORECAST_BASE_URL,
    AEMO_PREDISPATCH_PRICES_URL,
    AMBER_API_BASE_URL,
    NEM_REGIONS,
)

_LOGGER = logging.getLogger(__name__)

# Region timezone mapping
REGION_TIMEZONES = {
    "NSW1": "Australia/Sydney",
    "QLD1": "Australia/Brisbane",
    "VIC1": "Australia/Melbourne",
    "SA1": "Australia/Adelaide",
    "TAS1": "Australia/Hobart",
}


class AEMOClient:
    """Client for fetching AEMO wholesale electricity prices."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the AEMO client."""
        self._session = session
        self._forecast_cache: dict[str, Any] = {}
        self._forecast_cache_time: datetime | None = None

    async def get_current_prices(self) -> dict[str, dict[str, Any]]:
        """Fetch current 5-minute dispatch prices for all NEM regions.

        Returns:
            Dict mapping region code to price data:
            {
                'NSW1': {'price': 72.06, 'timestamp': '...', 'demand': 8500},
                ...
            }
        """
        try:
            async with self._session.get(
                AEMO_CURRENT_PRICE_URL,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.error("AEMO API returned status %s", response.status)
                    return {}

                data = await response.json()

                prices = {}
                for item in data.get("ELEC_NEM_SUMMARY", []):
                    region = item.get("REGIONID")
                    if region in NEM_REGIONS:
                        prices[region] = {
                            "price": float(item.get("PRICE", 0)),  # $/MWh
                            "price_cents": float(item.get("PRICE", 0)) / 10,  # c/kWh
                            "timestamp": item.get("SETTLEMENTDATE"),
                            "demand": float(item.get("TOTALDEMAND", 0)),
                            "status": item.get("PRICESTATUS", "FIRM"),
                        }

                return prices

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout fetching AEMO current prices")
            return {}
        except Exception as e:
            _LOGGER.error("Error fetching AEMO current prices: %s", e)
            return {}

    async def get_price_forecast(
        self, region: str, periods: int = 48
    ) -> list[dict[str, Any]]:
        """Fetch pre-dispatch price forecast for a region.

        Args:
            region: NEM region code (NSW1, QLD1, VIC1, SA1, TAS1)
            periods: Number of 30-minute periods to return (default 48 = 24 hours)

        Returns:
            List of forecast periods in Amber-compatible format:
            [
                {'nemTime': '...', 'perKwh': 25.5, 'wholesaleKWHPrice': 0.255},
                ...
            ]
        """
        # Check cache (update every 30 minutes)
        now = datetime.now(ZoneInfo("Australia/Sydney"))
        if (
            self._forecast_cache_time
            and (now - self._forecast_cache_time).total_seconds() < 1800
            and region in self._forecast_cache
        ):
            cached = self._forecast_cache.get(region, [])
            return cached[:periods] if cached else []

        try:
            # Try JSON API first (more reliable)
            forecast_data = await self._fetch_predispatch_json(region)

            # Fall back to ZIP parsing if JSON fails
            if not forecast_data:
                _LOGGER.debug("JSON API returned no data, trying ZIP fallback")
                forecast_data = await self._fetch_predispatch_report(region)

            if forecast_data:
                self._forecast_cache[region] = forecast_data
                self._forecast_cache_time = now
                return forecast_data[:periods]

            return []

        except Exception as e:
            _LOGGER.error("Error fetching AEMO forecast: %s", e)
            return self._forecast_cache.get(region, [])[:periods]

    async def _fetch_predispatch_json(self, region: str) -> list[dict[str, Any]]:
        """Fetch pre-dispatch prices from AEMO JSON API."""
        forecasts = []

        try:
            # Fetch 5-minute pre-dispatch data
            async with self._session.get(
                AEMO_5MIN_PREDISPATCH_URL,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.debug("5MIN_PREDISPATCH API returned status %s", response.status)
                else:
                    data = await response.json()
                    for item in data.get("5MIN_PREDISPATCH", []):
                        if item.get("REGIONID") == region:
                            timestamp = item.get("INTERVAL_DATETIME", "")
                            rrp = float(item.get("RRP", 0))

                            # Convert $/MWh to c/kWh
                            price_cents = rrp / 10
                            price_dollars = rrp / 1000

                            forecasts.append({
                                "nemTime": timestamp,
                                "perKwh": price_cents,
                                "wholesaleKWHPrice": price_dollars,
                                "price_mwh": rrp,
                            })

            # Also fetch 30-minute pre-dispatch for longer horizon
            async with self._session.get(
                AEMO_PREDISPATCH_PRICES_URL,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.debug("PREDISPATCH_PRICES API returned status %s", response.status)
                else:
                    data = await response.json()
                    for item in data.get("PREDISPATCH_PRICES", []):
                        if item.get("REGIONID") == region:
                            timestamp = item.get("DATETIME", "") or item.get("INTERVAL_DATETIME", "")
                            rrp = float(item.get("RRP", 0))

                            # Convert $/MWh to c/kWh
                            price_cents = rrp / 10
                            price_dollars = rrp / 1000

                            forecasts.append({
                                "nemTime": timestamp,
                                "perKwh": price_cents,
                                "wholesaleKWHPrice": price_dollars,
                                "price_mwh": rrp,
                            })

            # Sort by timestamp and remove duplicates
            seen = set()
            unique_forecasts = []
            for f in sorted(forecasts, key=lambda x: x["nemTime"]):
                if f["nemTime"] and f["nemTime"] not in seen:
                    seen.add(f["nemTime"])
                    unique_forecasts.append(f)

            _LOGGER.debug("AEMO JSON API returned %d forecast periods for %s", len(unique_forecasts), region)
            return unique_forecasts

        except Exception as e:
            _LOGGER.error("Error fetching AEMO JSON forecast: %s", e)
            return []

    async def _fetch_predispatch_report(self, region: str) -> list[dict[str, Any]]:
        """Fetch and parse AEMO pre-dispatch report from ZIP files."""
        try:
            # Get directory listing to find latest report
            async with self._session.get(
                AEMO_FORECAST_BASE_URL,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.error("Failed to list AEMO reports: %s", response.status)
                    return []

                html = await response.text()

            # Find the latest PUBLIC_PREDISPATCH file
            import re
            pattern = r'PUBLIC_PREDISPATCH_\d{8}_\d{6}[^"]*\.zip'
            matches = re.findall(pattern, html)

            if not matches:
                _LOGGER.warning("No pre-dispatch reports found")
                return []

            # Get the latest file
            latest_file = sorted(matches)[-1]
            file_url = f"{AEMO_FORECAST_BASE_URL}{latest_file}"

            # Download and parse the ZIP file
            async with self._session.get(
                file_url,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status != 200:
                    _LOGGER.error("Failed to download report: %s", response.status)
                    return []

                content = await response.read()

            # Extract and parse CSV from ZIP
            return self._parse_predispatch_zip(content, region)

        except Exception as e:
            _LOGGER.error("Error fetching pre-dispatch report: %s", e)
            return []

    def _parse_predispatch_zip(
        self, content: bytes, region: str
    ) -> list[dict[str, Any]]:
        """Parse pre-dispatch ZIP file and extract price data."""
        forecasts = []

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for filename in zf.namelist():
                    if "REGION" in filename.upper():
                        with zf.open(filename) as f:
                            # Read CSV content
                            csv_content = f.read().decode("utf-8")
                            reader = csv.reader(io.StringIO(csv_content))

                            for row in reader:
                                # Skip header rows and metadata
                                if len(row) < 8 or row[0] not in ("D", "I"):
                                    continue

                                # PDREGION format: D,PREDISPATCH,REGION_SOLUTION,...
                                if "REGION" in row[2].upper():
                                    row_region = row[6] if len(row) > 6 else ""
                                    if row_region == region:
                                        try:
                                            timestamp = row[7] if len(row) > 7 else ""
                                            rrp = float(row[8]) if len(row) > 8 else 0

                                            # Convert $/MWh to c/kWh
                                            price_cents = rrp / 10
                                            price_dollars = rrp / 1000

                                            forecasts.append({
                                                "nemTime": timestamp,
                                                "perKwh": price_cents,
                                                "wholesaleKWHPrice": price_dollars,
                                                "price_mwh": rrp,
                                            })
                                        except (ValueError, IndexError):
                                            continue

            # Sort by timestamp and remove duplicates
            seen = set()
            unique_forecasts = []
            for f in sorted(forecasts, key=lambda x: x["nemTime"]):
                if f["nemTime"] not in seen:
                    seen.add(f["nemTime"])
                    unique_forecasts.append(f)

            _LOGGER.debug("AEMO ZIP returned %d forecast periods for %s", len(unique_forecasts), region)
            return unique_forecasts

        except Exception as e:
            _LOGGER.error("Error parsing pre-dispatch ZIP: %s", e)
            return []


class AmberClient:
    """Client for fetching Amber Electric prices."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        site_id: str | None = None,
    ) -> None:
        """Initialize the Amber client."""
        self._session = session
        self._api_key = api_key
        self._site_id = site_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    async def get_sites(self) -> list[dict[str, Any]]:
        """Get list of Amber sites for the account."""
        try:
            async with self._session.get(
                f"{AMBER_API_BASE_URL}/sites",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    _LOGGER.error("Amber API returned status %s", response.status)
                    return []

                return await response.json()

        except Exception as e:
            _LOGGER.error("Error fetching Amber sites: %s", e)
            return []

    async def get_current_prices(self) -> list[dict[str, Any]]:
        """Get current prices for the configured site."""
        site_id = self._site_id

        if not site_id:
            sites = await self.get_sites()
            if sites:
                site_id = sites[0].get("id")
            else:
                return []

        try:
            async with self._session.get(
                f"{AMBER_API_BASE_URL}/sites/{site_id}/prices/current",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    _LOGGER.error("Amber prices API returned status %s", response.status)
                    return []

                return await response.json()

        except Exception as e:
            _LOGGER.error("Error fetching Amber current prices: %s", e)
            return []

    async def get_price_forecast(
        self,
        next_hours: int = 48,
        resolution: int = 30,
    ) -> list[dict[str, Any]]:
        """Get price forecast for the configured site.

        Args:
            next_hours: Number of hours to forecast
            resolution: Resolution in minutes (5 or 30)

        Returns:
            List of forecast intervals with price data
        """
        site_id = self._site_id

        if not site_id:
            sites = await self.get_sites()
            if sites:
                site_id = sites[0].get("id")
            else:
                return []

        try:
            params = {
                "next": next_hours,
                "resolution": resolution,
            }

            async with self._session.get(
                f"{AMBER_API_BASE_URL}/sites/{site_id}/prices",
                headers=self._headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    _LOGGER.error("Amber forecast API returned status %s", response.status)
                    return []

                return await response.json()

        except Exception as e:
            _LOGGER.error("Error fetching Amber forecast: %s", e)
            return []

    def extract_wholesale_price(self, price_data: dict[str, Any]) -> float:
        """Extract wholesale price from Amber price data.

        Args:
            price_data: Single price interval from Amber API

        Returns:
            Wholesale price in c/kWh
        """
        # Amber provides wholesaleKWHPrice in $/kWh, convert to c/kWh
        wholesale = price_data.get("wholesaleKWHPrice", 0)
        return wholesale * 100  # Convert $/kWh to c/kWh
