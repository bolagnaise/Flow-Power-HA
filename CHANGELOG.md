# Changelog

## v1.6.8

### Fix: KWatch Forecast Keeps The Next Upcoming Slot

- KWatch forecast updates now merge the 5-minute and 30-minute predispatch feeds instead of letting the 30-minute feed replace the finer-grained near-term data.
- This restores the missing next forecast slot reported in `#21` while still keeping the later half-hour forecast periods.
- Added regression coverage to keep the near-slot merge behavior locked in.

### Docs: Clarify Flow Power Pricing Input Priority

- Updated the README to match the shipped `v1.6.7` behavior: Flow Power import and forecast pricing now follows the same input priority as PowerSync.
- Added a direct regression test for the import-price path so portal/account `twap_import` remains the preferred input over the rolling wholesale TWAP when both are available.

## v1.6.7

### Fix: PowerSync-Aligned Flow Power Pricing

- Import price and forecast calculations now use the same Flow Power pricing input priority as PowerSync: TWAP override, KWatch/portal account TWAP, rolling TWAP, then fallback constants.
- Added an optional TWAP override setting and import price attributes showing the active TWAP, BPEA, GST, and pricing source for easier troubleshooting.
- KWatch runtime forecasts keep requesting the first upcoming half-hour slot so forecast attributes start at the next slot instead of skipping ahead.
- Added pricing regression coverage for the PowerSync-compatible helper path.

## v1.6.6

### Fix: KWatch Polling No Longer Drifts After Tariff Refreshes

- Network tariff refreshes now publish the recalculated import price to entities without rescheduling the coordinator's main polling timer, so KWatch current-price polling keeps running on schedule.
- This fixes the regression where import and wholesale price sensors could sit flat for long periods even though the 5-minute tariff callback was still firing.
- Added a regression check to keep the manual tariff refresh path separate from the main coordinator reschedule path.

### Fix: Apex Forecast Arrays Accept ISO Timestamps Again

- Forecast sensor timestamp parsing now accepts both the older slash-separated AEMO format and the ISO timestamps that Flow Power has started returning, restoring `apex_forecast_import` and `apex_forecast_wholesale` values.

### Feature: Optional Happy Hour Export Rate Override

- Added an optional `Happy Hour Export Rate ($/kWh)` setting for customers on non-standard plans, including 50 c/kWh variants, and applied the override consistently in both current export pricing and time-based calculations.

## v1.6.5

### Fix: KWatch API Key Setup Validation

- Flow Power KWatch API key validation now checks a wider dispatch window during setup so valid keys are not rejected just because no price landed in the most recent 5-minute slice.
- Setup now also probes the 5-minute predispatch endpoint and accepts the API key when dispatch pricing is available even if site metadata or forecast data is temporarily sparse.
- Added regression coverage for the wider dispatch probe, 5-minute forecast fallback, and the price-only success path used by config flow validation.

## v1.6.4

### Fix: KWatch Forecast Parsing and TOU Alignment

- KWatch forecast parsing now accepts uppercase and underscore-separated field names that Flow Power has started returning in some API responses.
- Forecast periods with missing timestamps are now inferred from the surrounding sequence instead of collapsing onto the current time, so sparse KWatch payloads still produce a complete ordered forecast.
- Forecast network tariff lookup now applies the tariff for the interval being priced instead of the interval-end timestamp, fixing the half-hour TOU shift reported in forecast sensors.
- Added regression coverage for mixed-format KWatch forecast payloads and the forecast tariff boundary case.

## v1.6.3

### Feature: Flow Power KWatch API Support

- Added Flow Power KWatch API key setup as the primary Flow Power connection path.
- KWatch API entries now fetch current dispatch pricing and forecast pricing directly from Flow Power, with AEMO/NEMWEB retained as a fallback if the API price fetch fails.
- Added optional residential site selection so account summary metrics such as PEA, TWAP, LWAP, demand, and loss factors can come from `GetResidentialSiteSummary`.
- Existing portal email/password/MFA entries remain supported as a legacy compatibility path.
- Added regression coverage for KWatch API key headers, nested JSON responses, price normalization, residential summary normalization, and price-only validation when site metadata is unavailable.

## v1.6.2

### Fix: Network Tariff Timing and Import Price Refresh

- Network tariff lookup now uses the NEM dispatch interval end, matching how AEMO dispatch periods are labelled.
- Import price now updates immediately when the network tariff refresh changes, instead of waiting for the next AEMO dispatch fetch.
- Import price attributes now expose `network_tou_adjustment_cents` and `price_without_network_tou_adjustment_cents`, so app-vs-sensor differences can be traced directly.
- Network tariff and portal account sensors now round noisy decimal values for cleaner dashboards.

## v1.6.1

### Fix: Use Raw Wholesale TWAP for PEA Calculations

When the Flow Power portal is connected, import price and forecast calculations now keep using the integration's rolling raw wholesale TWAP instead of substituting the portal account TWAP. The portal TWAP remains available on account sensors, but it is not the correct input for the PEA formula and could make import prices read too low.

Also updates the `aemo-to-tariff` dependency to `0.7.15`, which includes Endeavour Energy N73 tariff support from upstream.

Closes #4 and #9.

## v1.6.0

### Feature: Adaptive AEMO Polling (PR #5 by @pvandenh)

Replaces the fixed 30-second AEMO polling timer with a 3-tier adaptive strategy that catches new wholesale prices within 1-3 seconds of publication on NEMWEB.

#### Polling tiers
- **WAIT** (45s interval) — well before the next 5-minute dispatch boundary, skips NEMWEB entirely
- **PRE-ACTIVE** (5s interval) — 10 seconds before the boundary, starts gentle polling
- **ACTIVE** (1s interval) — 15 seconds after the boundary, polls aggressively until the new dispatch file appears

#### Other improvements
- Dispatch file results cached by filename — no redundant ZIP downloads during ACTIVE mode
- Forecast fetch gated on new dispatch — predispatch endpoint never polled at 1s intervals
- Portal fetch moved outside WAIT gate — 30-minute portal refresh honoured during WAIT mode
- Boundary initialization guard prevents premature WAIT mode on startup

## v1.5.6

### Feature: Import Price History for ApexCharts

The import price sensor now exposes `apex_import_history` — a pre-built `[[epoch_ms, cents], ...]` series for ApexCharts, matching the format of `apex_forecast_import` on the forecast sensor. Keeps up to 48 hours of history.

Example ApexCharts card comparing actual vs forecast:
```yaml
type: custom:apexcharts-card
header:
  title: Import Price vs Forecast
series:
  - entity: sensor.flow_power_qld1_import_price
    name: Actual Import
    unit: c/kWh
    data_generator: |
      return entity.attributes.apex_import_history;
  - entity: sensor.flow_power_qld1_price_forecast
    name: Forecast Import
    unit: c/kWh
    data_generator: |
      return entity.attributes.apex_forecast_import;
  - entity: sensor.flow_power_qld1_price_forecast
    name: Wholesale
    unit: c/kWh
    data_generator: |
      return entity.attributes.apex_forecast_wholesale;
```

## v1.5.5

### Fix: Forecast Not Including Network Tariff (TOU Rates)

The price forecast was showing raw AEMO wholesale prices without network tariff, causing an inverted pattern (cheap during day, expensive at night) compared to the import price sensor which correctly applied TOU rates.

**Root cause:** AEMO pre-dispatch timestamps use `"2026/04/01 13:30:00"` format (forward slashes), but `datetime.fromisoformat()` only accepts `"2026-04-01T13:30:00"` format (dashes). The timestamp parse silently failed for every forecast period, so no network tariff was ever applied to forecasts.

## v1.5.4

### Fix: Energex Tariff URL + Translation Cache

- Fixed Energex tariff URL — was pointing to Ergon's page, now points to Energex's residential tariffs page
- If tariff code step shows raw field names after re-auth, restart HA to clear the translation cache

## v1.5.3

### Fix: Missing Translations for Tariff Code Step

The tariff code step was showing raw field names (`fp_tariff_code`) and no description because `translations/en.json` was out of sync with `strings.json`. Now synced — the tariff step shows the proper title, field label, distributor name, and lookup URL.

## v1.5.2

### Fix: Tariff Code Step After Re-authentication

The tariff code step now appears after re-authentication completes, so you don't lose your network/tariff settings when re-authing. Form data from the settings page is preserved through the reauth flow.

## v1.5.1

### Improvement: Tariff Code on Separate Page

The tariff code selection is now a separate step in the options flow. After selecting your distributor and clicking submit, you're taken to a dedicated tariff code page with a dropdown of available codes and a link to your distributor's tariff lookup page.

## v1.5.0

### Fix: Session Persistence + Tariff Code UX

Combines all session persistence fixes and tariff code improvements:

#### Session fixes (root cause: KeepAlive "Success" string mismatch)
- Server returns `"Success"` (JSON-quoted) but code checked for `Success` (unquoted) — session was alive but always rejected
- Cookie secure/httponly flags now preserved across save/restore
- Dead sessions no longer follow B2C redirects (prevented cookie jar pollution)
- Cookies persisted to disk after every keepalive (not just every 30-min data fetch)

#### Tariff code improvements
- Options flow now shows a tariff code dropdown (was plain text input)
- Tariff selection shows distributor-specific lookup URL and bill hint
- Hint on distributor field: save after changing to load tariff codes

## v1.4.9

### Improvement: Tariff Code Lookup Hints

The tariff code selection step now shows your distributor name and a direct link to their tariff lookup page. Your tariff code is also usually listed on your electricity bill under "tariff" or "network tariff".

Closes #3.

## v1.4.8

### Fix: Tariff Code Dropdown in Options Flow

The options flow showed a plain text input for tariff code instead of a dropdown. Now loads available tariff codes for the selected network and shows a dropdown selector, matching the initial config flow behavior.

## v1.4.7

### Fix: Session Restore — KeepAlive "Success" String Mismatch

Root cause found: the kWatch server returns `"Success"` (JSON-quoted) but the code compared against `Success` (unquoted). The session was actually alive on every restart — we just rejected it because of the extra quotes. Also affected the ongoing keepalive, meaning every 30-min keepalive was falsely marking the session as expired.

## v1.4.6

### Diagnostic: Session Cookie Debugging

Added info-level logging to diagnose session loss on restart. Shows cookie names/domains at export, import, and restore so we can see exactly what's in the jar and what the server returns.

## v1.4.5

### Fix: Session Dropping Daily

Investigated how the kWatch portal keeps Chrome sessions alive for weeks — turns out it's purely cookie-based (no B2C SSO tokens). Found three bugs in how the integration handles session cookies that caused daily session loss.

#### What changed
- **Cookie flags preserved** — the `.AspNet.Cookies` session cookie has `Secure` and `HttpOnly` flags which were being lost during save/restore, potentially causing aiohttp to not send the cookie correctly
- **Cookie jar pollution fixed** — when a session was dead, `restore_session()` followed redirects into the B2C login page, polluting the cookie jar with stale artifacts that could interfere with re-authentication
- **Cookies persisted after keepalive** — ASP.NET sliding expiration renews cookies on each request, but the renewed cookie was only saved to disk every 30 minutes (on data fetch). If HA crashed in between, the refreshed cookie was lost. Now saved immediately after each keepalive

#### After updating
Re-authenticate once (Options > Re-authenticate Flow Power). Sessions should now survive much longer between re-auths.

## v1.4.4

### Improvement: Auto-Reauth — Only SMS Code Needed

When your Flow Power portal session expires, the integration now **automatically re-submits your stored credentials** in the background. You'll only need to enter the SMS verification code — no more re-typing your email and password.

#### What changed
- **Automatic credential submission** — when the session expires and cookie restore fails, the integration re-authenticates with your saved email/password automatically
- **MFA-only re-auth flow** — when you go to Configure > Re-authenticate, the credentials step is skipped and you go straight to the SMS code entry
- **New repair notification** — a specific "SMS verification needed" alert appears instead of the generic "session expired" message, so you know exactly what to do

#### After updating
No action needed — next time your session expires, you'll see the streamlined MFA-only prompt instead of the full login form.

## v1.3.1

### Fix: Portal Session Persistence (Cookie-Based)

v1.3.0's token exchange approach failed because Flow Power's B2C app is a confidential client. This release switches to **cookie persistence** — the kWatch session cookies are saved to HA storage after each successful login and data fetch, and restored on restart.

#### What changed
- **Cookie persistence** — after MFA verification, the kWatch session cookies are saved to persistent storage
- **Seamless restart recovery** — on HA restart, stored cookies are loaded and validated via KeepAlive
- **Mid-session recovery** — if the session expires during operation, the integration attempts to restore from stored cookies
- **Cookies kept fresh** — cookies are re-saved after every successful data fetch (every 30 minutes)

#### After updating
Re-authenticate once (Options > Re-authenticate Flow Power). After that, restarts should restore your session automatically as long as the server-side session hasn't expired.

## v1.3.0 (superseded by v1.3.1)

### Fix: Portal Session Survives HA Restarts (token exchange — did not work)

Attempted B2C OAuth2 token refresh, but Flow Power's B2C app registration is a confidential client so the token exchange fails with `AADB2C90085`. Superseded by v1.3.1's cookie persistence approach.

## v1.2.0

### New: Flow Power Portal Login

You can now log in directly to your Flow Power account to get **actual pricing data** from Flow Power's billing system, instead of relying on calculated estimates.

#### What's new
- **New price source: "Flow Power (Portal login)"** — authenticates to your Flow Power account at [flowpower.kwatch.com.au](https://flowpower.kwatch.com.au) via email + SMS verification
- **New sensor: Account PEA (Actual)** — shows your real PEA value from Flow Power, with attributes for LWAP, TWAP, average RRP, DLF (site losses), and more
- **Portal account metrics** — exposes Flow Power's actual account TWAP values in sensors. Note: v1.6.1 corrected import and forecast calculations to use the raw wholesale rolling TWAP required by the PEA formula.
- **Re-authentication** — if your portal session expires, use the options flow to re-authenticate without removing the integration

#### How it works
- Select "Flow Power (Portal login)" during setup
- Enter your Flow Power portal email and password
- Enter the SMS verification code sent to your phone
- The integration fetches your account data every 30 minutes and uses AEMO for real-time spot prices and forecasts

#### Account sensor attributes
| Attribute | Description |
|-----------|-------------|
| `lwap` | Load-Weighted Average Price (c/kWh) |
| `lwap_import` | LWAP for imports only |
| `twap` | Time-Weighted Average Price (c/kWh) |
| `avg_rrp` | Average spot price (c/kWh) |
| `pea_30_days` | 30-day PEA (net) |
| `pea_30_import` | 30-day PEA (import only) |
| `pea_actual` | Current PEA |
| `pea_target` | PEA target |
| `site_losses_dlf` | Distribution Loss Factor |
| `gst_multiplier` | GST multiplier |
| `avg_usage_kw` | 30-day average demand (kW) |

## v1.1.0

- Initial release with AEMO and Amber Electric price sources
- PEA calculation with dynamic TWAP
- Happy Hour export pricing
- EMHASS-compatible forecast sensor
