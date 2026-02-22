"""API clients for AEMO and Amber price data."""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import zipfile
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from .const import (
    AEMO_CURRENT_PRICE_URL,
    AEMO_DISPATCH_URL,
    AEMO_FORECAST_BASE_URL,
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
    """Client for fetching AEMO wholesale electricity prices.

    Uses NEMWEB dispatch ZIP files for faster updates than the JSON API.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the AEMO client."""
        self._session = session
        self._forecast_cache: dict[str, Any] = {}
        self._forecast_cache_time: datetime | None = None
        self._last_dispatch_file: str | None = None

    async def get_current_prices(self) -> dict[str, dict[str, Any]]:
        """Fetch current 5-minute dispatch prices for all NEM regions.

        Uses NEMWEB dispatch ZIP files for faster updates.

        Returns:
            Dict mapping region code to price data:
            {
                'NSW1': {'price': 72.06, 'timestamp': '...', 'demand': 8500},
                ...
            }
        """
        try:
            # Get directory listing to find latest dispatch file
            async with self._session.get(
                AEMO_DISPATCH_URL,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.error("NEMWEB dispatch listing returned status %s", response.status)
                    return await self._get_current_prices_fallback()

                html = await response.text()

            # Find the latest PUBLIC_DISPATCHIS file
            # Pattern: PUBLIC_DISPATCHIS_YYYYMMDDHHMM_SEQUENCE.zip
            pattern = r'PUBLIC_DISPATCHIS_\d{12}_\d+\.zip'
            matches = re.findall(pattern, html)

            if not matches:
                _LOGGER.warning("No dispatch files found, using fallback API")
                return await self._get_current_prices_fallback()

            # Get the latest file (sorted by timestamp and sequence)
            latest_file = sorted(matches)[-1]

            # Skip if we already processed this file
            if latest_file == self._last_dispatch_file:
                _LOGGER.debug("Dispatch file unchanged: %s", latest_file)
                # Still need to return current data - fall through to download

            file_url = f"{AEMO_DISPATCH_URL}{latest_file}"

            # Download and parse the ZIP file
            async with self._session.get(
                file_url,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.error("Failed to download dispatch file: %s", response.status)
                    return await self._get_current_prices_fallback()

                content = await response.read()

            self._last_dispatch_file = latest_file
            prices = self._parse_dispatch_zip(content)

            if prices:
                _LOGGER.debug("NEMWEB dispatch: %s -> %d regions", latest_file, len(prices))
                return prices
            else:
                _LOGGER.warning("No prices parsed from dispatch file")
                return await self._get_current_prices_fallback()

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout fetching NEMWEB dispatch")
            return await self._get_current_prices_fallback()
        except Exception as e:
            _LOGGER.error("Error fetching NEMWEB dispatch: %s", e)
            return await self._get_current_prices_fallback()

    def _parse_dispatch_zip(self, content: bytes) -> dict[str, dict[str, Any]]:
        """Parse dispatch ZIP file and extract price data.

        Looks for DISPATCH.PRICE table rows in the CSV:
        D,DISPATCH,PRICE,4,SETTLEMENTDATE,RUNNO,REGIONID,DISPATCHINTERVAL,INTERVENTION,...,RRP,...
        """
        prices: dict[str, dict[str, Any]] = {}

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for filename in zf.namelist():
                    if not filename.upper().endswith('.CSV'):
                        continue

                    with zf.open(filename) as f:
                        csv_content = f.read().decode("utf-8")
                        reader = csv.reader(io.StringIO(csv_content))

                        for row in reader:
                            if len(row) < 10:
                                continue

                            # Look for DISPATCH.PRICE data rows with intervention=0
                            # Format: D,DISPATCH,PRICE,4,timestamp,runno,REGIONID,interval,intervention,...,RRP
                            if (
                                row[0] == "D"
                                and row[1] == "DISPATCH"
                                and row[2] == "PRICE"
                            ):
                                try:
                                    region = row[6]
                                    intervention = int(row[8]) if row[8] else 0

                                    # Only use non-intervention prices
                                    if intervention != 0:
                                        continue

                                    if region not in NEM_REGIONS:
                                        continue

                                    timestamp = row[4]
                                    # RRP is typically at index 9 for DISPATCH.PRICE
                                    rrp = float(row[9])

                                    prices[region] = {
                                        "price": rrp,  # $/MWh
                                        "price_cents": rrp / 10,  # c/kWh
                                        "timestamp": timestamp,
                                        "demand": 0,  # Not in this table
                                        "status": "FIRM",
                                    }

                                except (ValueError, IndexError) as e:
                                    _LOGGER.debug("Error parsing dispatch row: %s", e)
                                    continue

            return prices

        except Exception as e:
            _LOGGER.error("Error parsing dispatch ZIP: %s", e)
            return {}

    async def _get_current_prices_fallback(self) -> dict[str, dict[str, Any]]:
        """Fallback to JSON API if NEMWEB fails."""
        try:
            async with self._session.get(
                AEMO_CURRENT_PRICE_URL,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    _LOGGER.error("AEMO fallback API returned status %s", response.status)
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

                _LOGGER.debug("Fallback API returned %d regions", len(prices))
                return prices

        except Exception as e:
            _LOGGER.error("Fallback API also failed: %s", e)
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
            # Fetch from NEMWeb pre-dispatch reports (ZIP files)
            forecast_data = await self._fetch_predispatch_report(region)

            if forecast_data:
                self._forecast_cache[region] = forecast_data
                self._forecast_cache_time = now
                _LOGGER.info("Cached %d forecast periods for %s", len(forecast_data), region)
                return forecast_data[:periods]
            else:
                _LOGGER.warning("No forecast data returned for %s", region)
                # Don't cache empty results - try again next time
                return []

        except Exception as e:
            _LOGGER.error("Error fetching AEMO forecast: %s", e)
            cached = self._forecast_cache.get(region, [])
            if cached:
                _LOGGER.info("Returning %d cached forecast periods for %s", len(cached), region)
            return cached[:periods]

    async def _fetch_predispatch_report(self, region: str) -> list[dict[str, Any]]:
        """Fetch and parse AEMO pre-dispatch report from ZIP files."""
        _LOGGER.info("Fetching AEMO pre-dispatch report for %s", region)
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
            # Pattern: PUBLIC_PREDISPATCH_YYYYMMDDHHMM_YYYYMMDDHHMMSS_LEGACY.zip
            # Example: PUBLIC_PREDISPATCH_202512220830_20251222080217_LEGACY.zip
            pattern = r'PUBLIC_PREDISPATCH_\d{12}_\d{14}[^">\s]*\.zip'
            matches = re.findall(pattern, html)
            _LOGGER.info("Found %d pre-dispatch files", len(matches))

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
        """Parse pre-dispatch ZIP file and extract price data.

        CSV format: D,PDREGION,,5,PREDISPATCHSEQNO,RUNNO,REGIONID,PERIODID,RRP,...
        Example: D,PDREGION,,5,"2025/12/22 09:00:00",1,QLD1,"2025/12/22 09:30:00",35.73
        """
        forecasts = []

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for filename in zf.namelist():
                    # Process CSV files
                    if filename.upper().endswith('.CSV'):
                        with zf.open(filename) as f:
                            # Read CSV content
                            csv_content = f.read().decode("utf-8")
                            reader = csv.reader(io.StringIO(csv_content))

                            for row in reader:
                                # Skip rows that are too short
                                if len(row) < 9:
                                    continue

                                # Look for data rows with PDREGION format
                                # Format: D,PDREGION,,5,seqno,runno,REGIONID,PERIODID,RRP
                                if row[0] == "D" and row[1] == "PDREGION":
                                    row_region = row[6]
                                    if row_region == region:
                                        try:
                                            # PERIODID is the forecast timestamp
                                            timestamp = row[7].strip('"')
                                            rrp = float(row[8])

                                            # Convert $/MWh to c/kWh
                                            price_cents = rrp / 10
                                            price_dollars = rrp / 1000

                                            forecasts.append({
                                                "nemTime": timestamp,
                                                "perKwh": price_cents,
                                                "wholesaleKWHPrice": price_dollars,
                                                "price_mwh": rrp,
                                            })
                                        except (ValueError, IndexError) as e:
                                            _LOGGER.debug("Error parsing row: %s", e)
                                            continue

            # Sort by timestamp and remove duplicates
            seen = set()
            unique_forecasts = []
            for f in sorted(forecasts, key=lambda x: x["nemTime"]):
                if f["nemTime"] not in seen:
                    seen.add(f["nemTime"])
                    unique_forecasts.append(f)

            _LOGGER.info("AEMO ZIP parsed %d forecast periods for %s", len(unique_forecasts), region)
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
                if response.status == 401 or response.status == 403:
                    _LOGGER.error("Amber API authentication failed (status %s) - check your API key", response.status)
                    return []
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
                if response.status == 401 or response.status == 403:
                    _LOGGER.error("Amber forecast API authentication failed (status %s) - check your API key", response.status)
                    return []
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
        # Amber provides spotPerKwh in c/kWh (NEM spot price including GST)
        return price_data.get("spotPerKwh", 0)
