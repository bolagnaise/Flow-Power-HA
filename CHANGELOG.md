# Changelog

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
- **More accurate PEA calculations** — when using portal login, the integration uses Flow Power's actual TWAP instead of a self-calculated rolling average for all price calculations
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
