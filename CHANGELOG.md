# Changelog

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
