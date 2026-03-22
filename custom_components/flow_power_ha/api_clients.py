"""API clients for AEMO, Amber, and Flow Power portal price data."""
from __future__ import annotations

import asyncio
import csv
import html as html_mod
import io
import json
import logging
import re
import time as time_mod
import zipfile
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo

import aiohttp

from .const import (
    AEMO_CURRENT_PRICE_URL,
    AEMO_DISPATCH_URL,
    AEMO_FORECAST_BASE_URL,
    AMBER_API_BASE_URL,
    FLOWPOWER_BASE_URL,
    FLOWPOWER_B2C_POLICY,
    FLOWPOWER_B2C_TENANT,
    FLOWPOWER_CLIENT_ID,
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


class FlowPowerPortalClient:
    """Client for Flow Power kWatch portal.

    Authenticates via Azure AD B2C (email + password + SMS MFA)
    and fetches actual account data (PEA, LWAP, TWAP, etc.)
    directly from Flow Power's portal.
    """

    B2C_BASE = (
        f"https://{FLOWPOWER_B2C_TENANT}.b2clogin.com"
        f"/{FLOWPOWER_B2C_TENANT}.onmicrosoft.com"
        f"/{FLOWPOWER_B2C_POLICY}"
    )

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize the Flow Power portal client.

        If no session is provided, creates one with unsafe cookie jar
        (required for cross-domain B2C auth cookies).
        """
        if session is None:
            # Need unsafe=True so cookies from b2clogin.com are sent
            # back correctly across the B2C redirect flow
            jar = aiohttp.CookieJar(unsafe=True)
            self._session = aiohttp.ClientSession(cookie_jar=jar)
            self._owns_session = True
        else:
            self._session = session
            self._owns_session = False
        self._authenticated = False
        self._last_keepalive: float = 0
        # B2C auth state (populated during authenticate())
        self._csrf_token: str | None = None
        self._tx: str | None = None
        self._api_url: str | None = None
        self._cookies: dict[str, str] = {}  # Manual cookie store for B2C
        # Report GUIDs (fetched dynamically from /menu/allmenu after login)
        self._home_report_guid: str | None = None
        self._home_report_properties: str | None = None

    def _b2c_cookie_header(self) -> str:
        """Build a Cookie header string from stored cookies.

        aiohttp's cookie jar mangles the pipe character in B2C cookie
        names like 'x-ms-cpim-cache|xxx', so we build the header manually.
        """
        return "; ".join(
            f"{k}={v}" for k, v in self._cookies.items()
        )

    def _capture_cookies(self, resp: aiohttp.ClientResponse) -> None:
        """Capture Set-Cookie headers from a response into our cookie store."""
        for header_val in resp.headers.getall("Set-Cookie", []):
            # Parse "name=value; path=...; ..."
            parts = header_val.split(";")[0]  # Just name=value
            if "=" in parts:
                name, _, value = parts.partition("=")
                self._cookies[name.strip()] = value.strip()

    def _extract_b2c_settings(self, html: str, url: str) -> tuple[str | None, str | None]:
        """Extract CSRF token and transId from a B2C page.

        Azure AD B2C embeds a SETTINGS JavaScript object in the login page
        containing csrf and transId fields needed for API calls.
        """
        csrf = None
        tx = None

        # Pattern 1: SETTINGS JSON object (most common)
        settings_match = re.search(r'var\s+SETTINGS\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
        if settings_match:
            try:
                settings = json.loads(settings_match.group(1))
                csrf = settings.get("csrf")
                tx = settings.get("transId")
            except json.JSONDecodeError:
                pass

        # Pattern 2: Individual JSON fields in page
        if not csrf:
            m = re.search(r'"csrf"\s*:\s*"([^"]+)"', html)
            if m:
                csrf = m.group(1)

        if not tx:
            m = re.search(r'"transId"\s*:\s*"([^"]+)"', html)
            if m:
                tx = m.group(1)

        # Pattern 3: Extract from URL query params
        if not tx:
            m = re.search(r'[?&]tx=(StateProperties=[A-Za-z0-9%+=/_-]+)', url)
            if m:
                tx = m.group(1)

        # Pattern 4: Look in meta tags or hidden inputs
        if not csrf:
            m = re.search(r'name="csrf"\s+content="([^"]+)"', html)
            if m:
                csrf = m.group(1)

        return csrf, tx

    async def authenticate(self, email: str, password: str) -> dict[str, Any]:
        """Submit credentials to B2C and request SMS MFA.

        Returns:
            {"status": "mfa_required"} if SMS was sent.

        Raises:
            Exception on invalid credentials or network error.
        """
        # Step 1: Start from the kWatch portal which redirects to B2C
        # with a proper state parameter. This is required because B2C
        # validates that the request originated from the kWatch redirect chain.
        _LOGGER.debug("Flow Power: Loading portal to trigger B2C redirect")
        async with self._session.get(
            f"{FLOWPOWER_BASE_URL}/Home/Index",
            timeout=aiohttp.ClientTimeout(total=30),
            allow_redirects=True,
        ) as resp:
            page_html = await resp.text()
            page_url = str(resp.url)
            _LOGGER.debug(
                "Flow Power: B2C login page status=%s, url=%s, html_len=%d",
                resp.status, page_url[:120], len(page_html),
            )

        # Capture all cookies from the authorize redirect chain
        for c in self._session.cookie_jar:
            self._cookies[c.key] = c.value

        csrf, tx = self._extract_b2c_settings(page_html, page_url)

        if not csrf or not tx:
            _LOGGER.error(
                "Flow Power: Could not extract B2C tokens. "
                "csrf=%s, tx=%s, html_len=%d, url=%s",
                "found" if csrf else "MISSING",
                "found" if tx else "MISSING",
                len(page_html),
                page_url[:200],
            )
            raise ValueError(
                "Could not extract B2C auth tokens from login page"
            )

        self._csrf_token = csrf
        self._tx = tx
        self._login_page_url = page_url  # Save for Referer header
        # Use the actual policy path from the redirect (case-sensitive)
        self._b2c_base = page_url.split("/oauth2/")[0] if "/oauth2/" in page_url else self.B2C_BASE
        # Log cookies set by the authorize page
        b2c_cookies = list(self._session.cookie_jar)
        _LOGGER.debug(
            "Flow Power: B2C tokens extracted - csrf_len=%d, tx=%s, cookies=%d (%s)",
            len(csrf), tx[:80], len(b2c_cookies),
            ", ".join(c.key for c in b2c_cookies) if b2c_cookies else "none",
        )

        # Step 2: Submit email + password via SelfAsserted
        # Build the URL using the same base path as the login page
        # (B2C is case-sensitive on the policy path for cookie matching)
        tx_param = tx if tx.startswith("StateProperties=") else f"StateProperties={tx}"

        self_asserted_url = (
            f"{self._b2c_base}/SelfAsserted"
            f"?tx={tx_param}"
            f"&p={FLOWPOWER_B2C_POLICY}"
        )

        _LOGGER.debug(
            "Flow Power: POST SelfAsserted url=%s",
            self_asserted_url[:200],
        )

        async with aiohttp.ClientSession() as clean_session:
            async with clean_session.post(
                self_asserted_url,
                data={
                    "request_type": "RESPONSE",
                    "email": email,
                    "password": password,
                },
                headers={
                    "X-CSRF-TOKEN": self._csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Origin": f"https://{FLOWPOWER_B2C_TENANT}.b2clogin.com",
                    "Referer": self._login_page_url,
                    "Cookie": self._b2c_cookie_header(),
                },
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as resp:
                self._capture_cookies(resp)
                status = resp.status
                body = await resp.text()
                _LOGGER.debug(
                    "Flow Power: SelfAsserted status=%s, body=%s",
                    status,
                    body[:500] if body else "(empty)",
                )

        if status != 200:
            raise ValueError(f"Login failed with status {status}")

        if '"status":"400"' in body or "INCORRECT_PASSWORD" in body:
            raise ValueError("Invalid email or password")

        # Step 3: Confirm the sign-in (triggers MFA page)
        confirmed_url = (
            f"{self._b2c_base}/api/CombinedSigninAndSignup/confirmed"
            f"?rememberMe=true"
            f"&csrf_token={self._csrf_token}"
            f"&tx={tx_param}"
            f"&p={FLOWPOWER_B2C_POLICY}"
        )

        async with aiohttp.ClientSession() as cs:
            async with cs.get(
                confirmed_url,
                headers={"Cookie": self._b2c_cookie_header()},
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=True,
            ) as resp:
                self._capture_cookies(resp)
                mfa_html = await resp.text()
                mfa_url = str(resp.url)
                _LOGGER.debug(
                    "Flow Power: Confirmed page status=%s, html_len=%d, cookies=%d",
                    resp.status, len(mfa_html), len(self._cookies),
                )

        # The MFA page may have updated CSRF/tx
        new_csrf, new_tx = self._extract_b2c_settings(mfa_html, mfa_url)
        if new_csrf:
            self._csrf_token = new_csrf
        if new_tx:
            self._tx = new_tx

        # Step 4: Request SMS MFA
        tx_param = self._tx if self._tx.startswith("StateProperties=") else f"StateProperties={self._tx}"
        mfa_request_url = (
            f"{self._b2c_base}/Phonefactor/verify"
            f"?tx={tx_param}"
            f"&p={FLOWPOWER_B2C_POLICY}"
        )

        async with aiohttp.ClientSession() as cs:
            async with cs.post(
                mfa_request_url,
                data={
                    "request_type": "VERIFICATION_REQUEST",
                    "auth_type": "onewaysms",
                    "id": "1",
                },
                headers={
                    "X-CSRF-TOKEN": self._csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Cookie": self._b2c_cookie_header(),
                },
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as resp:
                self._capture_cookies(resp)
                mfa_status = resp.status
                mfa_resp = await resp.text()
                _LOGGER.debug(
                    "Flow Power: MFA request status=%s, body=%s",
                    mfa_status, mfa_resp[:200] if mfa_resp else "(empty)",
                )

        _LOGGER.info("Flow Power: SMS MFA code requested")
        return {"status": "mfa_required"}

    async def verify_mfa(self, code: str) -> bool:
        """Verify the SMS MFA code and establish portal session.

        Args:
            code: SMS verification code.

        Returns:
            True if authentication completed successfully.
        """
        if not self._csrf_token or not self._tx:
            raise ValueError("authenticate() must be called first")

        tx_param = self._tx if self._tx.startswith("StateProperties=") else f"StateProperties={self._tx}"

        # Step 1: Submit verification code
        verify_url = (
            f"{self._b2c_base}/Phonefactor/verify"
            f"?tx={tx_param}"
            f"&p={FLOWPOWER_B2C_POLICY}"
        )

        async with aiohttp.ClientSession() as cs:
            async with cs.post(
                verify_url,
                data={
                    "request_type": "VALIDATION_REQUEST",
                    "verification_code": code,
                },
                headers={
                    "X-CSRF-TOKEN": self._csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Cookie": self._b2c_cookie_header(),
                },
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as resp:
                self._capture_cookies(resp)
                body = await resp.text()
                _LOGGER.debug(
                    "Flow Power: MFA verify status=%s, body=%s",
                    resp.status, body[:200] if body else "(empty)",
                )

        if '"status":"400"' in body or "INCORRECT" in body.upper():
            return False

        # Step 2: Confirm MFA
        confirmed_url = (
            f"{self._b2c_base}/api/Phonefactor/confirmed"
            f"?csrf_token={self._csrf_token}"
            f"&tx={tx_param}"
            f"&p={FLOWPOWER_B2C_POLICY}"
        )

        async with aiohttp.ClientSession() as cs:
            async with cs.get(
                confirmed_url,
                headers={"Cookie": self._b2c_cookie_header()},
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as resp:
                self._capture_cookies(resp)
                _LOGGER.debug(
                    "Flow Power: MFA confirmed status=%s", resp.status,
                )
                # This should redirect back with code + id_token
                if resp.status in (200, 302):
                    redirect_html = await resp.text()
                else:
                    return False

        # The redirect may contain a form that auto-POSTs to kWatch
        # Extract code and id_token from the response
        _LOGGER.debug(
            "Flow Power: MFA confirmed html_len=%d, snippet=%s",
            len(redirect_html),
            redirect_html[:500] if redirect_html else "(empty)",
        )

        code_match = re.search(
            r"name=['\"]code['\"]\s+(?:id=['\"]code['\"]\s+)?value=['\"]([^'\"]+)['\"]", redirect_html
        )
        id_token_match = re.search(
            r"name=['\"]id_token['\"]\s+(?:id=['\"]id_token['\"]\s+)?value=['\"]([^'\"]+)['\"]", redirect_html
        )
        state_match = re.search(
            r"name=['\"]state['\"]\s+(?:id=['\"]state['\"]\s+)?value=['\"]([^'\"]+)['\"]", redirect_html
        )

        _LOGGER.debug(
            "Flow Power: code=%s, id_token=%s, state=%s",
            "found" if code_match else "MISSING",
            "found" if id_token_match else "MISSING",
            "found" if state_match else "MISSING",
        )

        if code_match and id_token_match:
            # Step 3: POST the callback to kWatch to establish session
            callback_data = {
                "code": code_match.group(1),
                "id_token": id_token_match.group(1),
            }
            if state_match:
                callback_data["state"] = state_match.group(1)

            async with self._session.post(
                f"{FLOWPOWER_BASE_URL}/Home/Index",
                data=callback_data,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=True,
            ) as resp:
                # Should redirect to Home/Index GET - session cookie now set
                await resp.text()
                self._authenticated = resp.status == 200
        else:
            # Try following redirects directly (the B2C may auto-redirect)
            async with self._session.get(
                f"{FLOWPOWER_BASE_URL}/Home/Index",
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=True,
            ) as resp:
                body = await resp.text()
                # If we get the app shell, we're authenticated
                self._authenticated = "allmenu" in body or "kWFormBase" in body

        _LOGGER.debug(
            "Flow Power: verify_mfa result - authenticated=%s",
            self._authenticated,
        )

        if self._authenticated:
            self._last_keepalive = time_mod.time()
            _LOGGER.info("Flow Power: Portal authentication successful")
            # Fetch menu to get report GUIDs for this account
            await self._fetch_menu_guids()

        return self._authenticated

    async def _fetch_menu_guids(self) -> None:
        """Fetch report GUIDs from the portal menu."""
        try:
            async with self._session.get(
                f"{FLOWPOWER_BASE_URL}/menu/allmenu",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "Flow Power: Failed to fetch menu (status %s)",
                        resp.status,
                    )
                    return

                menu = await resp.json()

            # Find the Home report
            for menu_item in menu.get("MenuItems", []):
                for sub in menu_item.get("SubMenuItems", []):
                    if sub.get("IsDefaultReport") or sub.get("Name") == "Home":
                        self._home_report_guid = sub.get("Link")
                        self._home_report_properties = sub.get(
                            "reportPropertiesGuid"
                        )
                        _LOGGER.debug(
                            "Flow Power: Home report GUID=%s, properties=%s",
                            self._home_report_guid,
                            self._home_report_properties,
                        )
                        return

            # Fallback to default report from menu root
            default_guid = menu.get("DefaultReportId")
            default_props = menu.get("DefaultReportPropertiesGuid")
            if default_guid:
                self._home_report_guid = default_guid
                self._home_report_properties = default_props

        except Exception as e:
            _LOGGER.error("Flow Power: Error fetching menu: %s", e)

    async def get_account_data(self) -> dict[str, Any] | None:
        """Fetch account data from the Flow Power portal.

        Returns dict with actual PEA, LWAP, TWAP, DLF, etc. from Flow Power,
        or None if session expired or request failed.
        """
        if not self._authenticated:
            return None

        if not self._home_report_guid:
            await self._fetch_menu_guids()
            if not self._home_report_guid:
                _LOGGER.error("Flow Power: No home report GUID available")
                return None

        # Keep session alive
        await self._keep_alive()

        try:
            # Load the Home report page which contains the userObject
            request_body = {
                "reportId": self._home_report_guid,
                "reportName": "Home",
                "reportProperties": self._home_report_properties,
                "reportSettings": None,
                "applicationSettings": json.dumps({
                    "applicationState": {},
                    "formBaseState": None,
                    "clientInfo": {
                        "loadTime": datetime.utcnow().strftime(
                            "%Y-%m-%dT%H:%M:%S.000Z"
                        ),
                        "timeZone": 600,
                        "currentTime": datetime.utcnow().strftime(
                            "%Y-%m-%dT%H:%M:%S.000Z"
                        ),
                    },
                }),
            }

            async with self._session.post(
                f"{FLOWPOWER_BASE_URL}/report/get?",
                json=request_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    if resp.status in (302, 401):
                        self._authenticated = False
                        _LOGGER.warning(
                            "Flow Power: Session expired (status %s)",
                            resp.status,
                        )
                    return None

                html_response = await resp.text()

            return self._parse_user_object(html_response)

        except Exception as e:
            _LOGGER.error("Flow Power: Error fetching account data: %s", e)
            return None

    def _parse_user_object(self, html: str) -> dict[str, Any] | None:
        """Extract the userObject from portal HTML response.

        The userObject is embedded as a data-userobject HTML attribute
        containing JSON with all the account pricing data.
        """
        # Look for data-userobject attribute
        match = re.search(r'data-userobject="([^"]+)"', html)
        if not match:
            # Try finding it in applicationSettings JSON
            match = re.search(r'"userObject"\s*:\s*(\{[^}]+\})', html)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
            _LOGGER.warning("Flow Power: Could not find userObject in response")
            return None

        # Decode HTML entities and parse JSON
        raw = match.group(1)
        decoded = html_mod.unescape(raw)

        try:
            user_obj = json.loads(decoded)
        except json.JSONDecodeError as e:
            _LOGGER.error("Flow Power: Failed to parse userObject JSON: %s", e)
            return None

        # Extract the pricing fields we care about
        return {
            "lwap": user_obj.get("LWAP"),
            "lwap_import": user_obj.get("LWAPImp"),
            "lwap_actual": user_obj.get("LWAPActual"),
            "lwap_import_actual": user_obj.get("LWAPImpActual"),
            "twap": user_obj.get("TWAP"),
            "twap_import": user_obj.get("TWAPImp"),
            "avg_rrp": user_obj.get("AvgRRP"),
            "avg_usage_kw": user_obj.get("AvgUsage"),
            "avg_import_usage_kw": user_obj.get("AvgImpUsage"),
            "max_usage_kw": user_obj.get("MaxUsage"),
            "total_intervals": user_obj.get("TotalInterval"),
            "pea_30_days": user_obj.get("PEA30Days"),
            "pea_30_import": user_obj.get("PEA30ImportDays"),
            "pea_actual": user_obj.get("PEAActual"),
            "pea_target": user_obj.get("PEATarget"),
            "pea_actual_import": user_obj.get("PEAActualImport"),
            "site_losses_dlf": user_obj.get("SiteLosses"),
            "gst_multiplier": user_obj.get("GST"),
        }

    async def _keep_alive(self) -> None:
        """Send keepalive to maintain session (every 5 minutes)."""
        now = time_mod.time()
        if now - self._last_keepalive < 290:
            return

        try:
            async with self._session.post(
                f"{FLOWPOWER_BASE_URL}/Account/KeepAlive",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                body = await resp.text()
                if body.strip() == "Success":
                    self._last_keepalive = now
                else:
                    _LOGGER.warning("Flow Power: KeepAlive returned: %s", body)
                    self._authenticated = False
        except Exception as e:
            _LOGGER.error("Flow Power: KeepAlive failed: %s", e)

    @property
    def is_authenticated(self) -> bool:
        """Return whether the portal session is active."""
        return self._authenticated
