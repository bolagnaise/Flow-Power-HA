# Flow Power HA

A Home Assistant integration that provides Flow Power electricity pricing sensors for EMHASS optimization.

## Features

- **Price Sources**: Supports AEMO (direct wholesale), Amber Electric, and Flow Power portal login
- **Flow Power Portal**: Login directly to your Flow Power account to get actual PEA, LWAP, and TWAP values from Flow Power's billing system
- **Connect Anytime**: Already set up with AEMO or Amber? Connect your Flow Power portal from the integration options — no need to reconfigure
- **PEA Calculation**: Implements Flow Power's Price Efficiency Adjustment formula
- **Happy Hour Export**: Automatic export pricing based on Flow Power Happy Hour (5:30pm-7:30pm)
- **EMHASS Compatible**: Price forecast sensor with attributes for EMHASS integration
- **Dynamic TWAP**: Auto-calculated 30-day rolling wholesale average for accurate PEA
- **Configurable**: Base rates and PEA settings

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "Flow Power HA" and install
3. Restart Home Assistant
4. Add the integration via Settings > Devices & Services > Add Integration

### Manual Installation

1. Copy the `custom_components/flow_power_ha` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant
3. Add the integration via Settings > Devices & Services > Add Integration

## Configuration

### Price Source

Choose between:
- **AEMO (Direct wholesale)**: Fetches prices directly from AEMO NEMWeb
- **Amber Electric**: Uses your Amber API key for pricing data
- **Flow Power (Portal login)**: Logs into your Flow Power account at [flowpower.kwatch.com.au](https://flowpower.kwatch.com.au) to fetch actual account data (PEA, LWAP, TWAP, DLF). Uses AEMO for real-time spot prices and forecasts. Requires SMS MFA during setup.

### Flow Power Portal

The Flow Power portal provides **actual account-specific** values directly from Flow Power's billing system, rather than calculated estimates. When connected, the integration uses Flow Power's real TWAP for more accurate PEA calculations across all price sources.

#### Setup during initial configuration

1. Select **"Flow Power (Portal login)"** as your price source
2. Enter your Flow Power portal email and password
3. Enter the SMS verification code sent to your registered phone number
4. Select your NEM region and configure pricing

#### Connect to an existing integration

Already set up with AEMO or Amber? You can connect your Flow Power portal account without removing the integration:

1. Go to **Settings > Devices & Services > Flow Power HA > Configure**
2. Toggle **"Connect Flow Power portal account"**
3. Submit, then enter your portal email and password
4. Enter the SMS verification code

#### Re-authentication

Portal sessions expire over time. If your session expires:

1. Go to **Settings > Devices & Services > Flow Power HA > Configure**
2. Toggle **"Re-authenticate with Flow Power portal"**
3. Submit, then re-enter your credentials and SMS code

The integration continues to work with calculated TWAP while the portal session is expired — re-authenticating simply restores the actual values.

### Pricing Settings

| Option | Default | Description |
|--------|---------|-------------|
| Base Rate | 34.0 c/kWh | Your Flow Power base energy rate (GST inclusive, as per PDS) |
| PEA Enabled | Yes | Apply Price Efficiency Adjustment |
| PEA Custom Value | - | Override calculated PEA with fixed value (c/kWh) |

## Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| `sensor.flow_power_import_price` | $/kWh | Current import price with PEA |
| `sensor.flow_power_export_price` | $/kWh | Current export price (Happy Hour aware) |
| `sensor.flow_power_wholesale_price` | c/kWh | Raw wholesale spot price |
| `sensor.flow_power_price_forecast` | $/kWh | Price forecast for EMHASS |
| `sensor.flow_power_twap` | c/kWh | 30-day rolling average wholesale price (TWAP) |
| `sensor.flow_power_account_pea_actual` | c/kWh | Actual PEA from Flow Power portal (portal only) |

### Account PEA Sensor Attributes

When the Flow Power portal is connected, the Account PEA sensor exposes these attributes:

| Attribute | Description |
|-----------|-------------|
| `lwap` | Load-Weighted Average Price (c/kWh) |
| `lwap_import` | LWAP for imports only (c/kWh) |
| `twap` | Time-Weighted Average Price (c/kWh) |
| `twap_import` | TWAP for imports only (c/kWh) |
| `avg_rrp` | Average spot price (c/kWh) |
| `pea_30_days` | 30-day PEA net (c/kWh) |
| `pea_30_import` | 30-day PEA import only (c/kWh) |
| `pea_actual` | Current PEA (c/kWh) |
| `pea_target` | PEA target / BPEA (c/kWh) |
| `bpea` | Benchmark PEA — average customer performance (c/kWh) |
| `bpea_import` | BPEA for imports only (c/kWh) |
| `cpea` | Customer PEA — your usage pattern vs average price, LWAP - TWAP (c/kWh) |
| `cpea_import` | CPEA for imports only (c/kWh) |
| `site_losses_dlf` | Distribution Loss Factor |
| `gst_multiplier` | GST multiplier |
| `avg_usage_kw` | 30-day average demand (kW) |
| `avg_import_usage_kw` | 30-day average import demand (kW) |
| `max_usage_kw` | Maximum demand (kW) |

## Price Forecast Chart

The forecast sensor includes pre-built data for charting the full forward price curve (next ~19 hours). Requires [ApexCharts Card](https://github.com/RomRider/apexcharts-card) from HACS.

```yaml
type: custom:apexcharts-card
header:
  title: Price Forecast
  show: true
graph_span: 24h
span:
  start: minute
series:
  - entity: sensor.flow_power_qld1_price_forecast
    data_generator: |
      return entity.attributes.apex_forecast_import;
    name: Import
    unit: c/kWh
    color: orange
  - entity: sensor.flow_power_qld1_price_forecast
    data_generator: |
      return entity.attributes.apex_forecast_wholesale;
    name: Wholesale
    unit: c/kWh
    color: cyan
```

Replace `qld1` with your region (`nsw1`, `vic1`, `sa1`, `tas1`).

| Attribute | Description |
|-----------|-------------|
| `apex_forecast_import` | Full forward curve of import prices in c/kWh (ready for ApexCharts) |
| `apex_forecast_wholesale` | Full forward curve of wholesale prices in c/kWh |

## EMHASS Integration

The `sensor.flow_power_price_forecast` sensor provides attributes compatible with EMHASS:

```yaml
state: 0.32  # Current price in $/kWh
attributes:
  forecast: [0.32, 0.28, 0.25, ...]  # 48 periods (24h at 30-min)
  timestamps: ["2024-01-01T00:00:00+10:00", ...]
  unit: "$/kWh"
```

### EMHASS Configuration Example

```yaml
# configuration.yaml
emhass:
  ...
  load_cost_forecast_method: sensor
  sensor_power_load_no_var_loads: sensor.home_load
  # Use the forecast sensor for price optimization
```

## Pricing Formula

### PEA (Price Efficiency Adjustment)

```
PEA = Wholesale - TWAP - BPEA
Final Rate = Base Rate + PEA

Where:
- TWAP = 30-day rolling average of wholesale spot prices (dynamic)
        or actual TWAP from Flow Power portal when connected
- BPEA = 1.7 c/kWh (Benchmark Price Efficiency Adjustment)
- Default Base Rate = 34.0 c/kWh
- When insufficient data (<1 hour), TWAP defaults to 8.0 c/kWh
```

### Export Rates (Happy Hour)

| Region | Happy Hour Rate |
|--------|----------------|
| NSW1 | 45 c/kWh |
| QLD1 | 45 c/kWh |
| SA1 | 45 c/kWh |
| VIC1 | 35 c/kWh |
| TAS1 | 0 c/kWh |

Happy Hour: 5:30pm - 7:30pm local time

## Support

For issues and feature requests, please open an issue on [GitHub](https://github.com/bolagnaise/Flow-Power-HA/issues).

## License

MIT License
