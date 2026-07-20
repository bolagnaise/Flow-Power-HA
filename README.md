# Flow Power HA

A Home Assistant integration for Flow Power electricity pricing sensors, compatible with EMHASS and HAEO.

## Features

- **Price Sources**: Supports AEMO (direct wholesale) and the official Flow Power Web Data API
- **Flow Power API**: Uses an API key to fetch KWatch prices and account values such as PEA, LWAP, and TWAP
- **Network Tariff (TOU)**: Select your electricity distributor and tariff code — network charges are applied to both current prices and forecasts
- **PEA Calculation**: Implements Flow Power's Price Efficiency Adjustment formula
- **Happy Hour Export**: Automatic export pricing based on Flow Power Happy Hour (5:30pm-7:30pm)
- **Optimizer Compatible**: Price forecast sensor with attributes for EMHASS and HAEO
- **Dynamic TWAP**: Auto-calculated 30-day rolling wholesale average for accurate PEA
- **ApexCharts Ready**: Pre-built data series for charting actual vs forecast prices
- **Configurable**: Base rates, PEA settings, and network tariff configuration

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
- **Flow Power API (KWatch)**: Uses the API key from **Flow Power App > More > Web Data Access** for current prices, forecasts, and available account-summary data. AEMO remains the fallback if a transient API price request fails.

### Network Tariff (TOU Pricing)

Select your electricity distributor (DNSP) and tariff code to include time-of-use network charges in both current prices and forecasts.

**Supported distributors:**

| Region | Distributors |
|--------|-------------|
| NSW | Ausgrid, Endeavour, Essential |
| QLD | Energex, Ergon |
| VIC | Powercor, CitiPower, AusNet, Jemena, United |
| SA | SAPN |
| TAS | TasNetworks |

Your tariff code is listed on your electricity bill under "tariff" or "network tariff". The integration shows a link to your distributor's tariff lookup page during configuration.

### Flow Power Web Data API

The customer-portal login workaround has been removed. The integration no longer stores a Flow Power email/password, performs SMS MFA, or sends automated requests to the customer portal.

To connect the supported API:

1. Open the Flow Power app and go to **More > Web Data Access**.
2. Copy the API key.
3. Select **Flow Power API (KWatch)** during setup, or open **Settings > Devices & Services > Flow Power HA > Configure**.
4. Enter the API key and select a residential site if one is returned.

Some valid keys provide prices but no residential-site summary. In that case pricing continues to work, but account-summary sensors remain unavailable. Import price and forecast PEA calculations use the manual TWAP override first, then the integration's rolling raw wholesale TWAP, then the fallback constant. API BPEA and GST values are used when available.

### Pricing Settings

| Option | Default | Description |
|--------|---------|-------------|
| Base Rate | 34.0 c/kWh | Your Flow Power base energy rate (GST inclusive, as per PDS) |
| PEA Enabled | Yes | Apply Price Efficiency Adjustment |
| PEA Custom Value | - | Override calculated PEA with fixed value (c/kWh) |
| Electricity Distributor | - | Your DNSP for network tariff TOU rates |
| Tariff Code | - | Your network tariff code |

## Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| `sensor.flow_power_<region>_import_price` | $/kWh | Current import price with PEA and network tariff |
| `sensor.flow_power_<region>_export_price` | $/kWh | Current export price (Happy Hour aware) |
| `sensor.flow_power_<region>_wholesale_price` | c/kWh | Raw wholesale spot price |
| `sensor.flow_power_<region>_price_forecast` | $/kWh | Price forecast for EMHASS and HAEO |
| `sensor.flow_power_<region>_twap` | c/kWh | 30-day rolling average wholesale price (TWAP) |
| `sensor.flow_power_<region>_network_tariff` | c/kWh | Current network tariff rate |
| `sensor.flow_power_<region>_account_pea_actual` | c/kWh | Actual PEA from the Flow Power API when account-summary access is available |

### Account PEA Sensor Attributes

When the Flow Power API provides account-summary data, the Account PEA sensor exposes these attributes:

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

## Price Charts

### Actual vs Forecast Price Chart

Compare actual import prices against the forecast using [ApexCharts Card](https://github.com/RomRider/apexcharts-card) from HACS.

```yaml
type: custom:apexcharts-card
header:
  title: Actual vs Forecast Price
  show: true
graph_span: 24h
span:
  start: day
series:
  - entity: sensor.flow_power_qld1_import_price
    data_generator: |
      return entity.attributes.apex_import_history;
    name: Actual Import
    unit: c/kWh
    color: "#4CAF50"
  - entity: sensor.flow_power_qld1_price_forecast
    data_generator: |
      return entity.attributes.apex_forecast_import;
    name: Forecast Import
    unit: c/kWh
    color: orange
  - entity: sensor.flow_power_qld1_price_forecast
    data_generator: |
      return entity.attributes.apex_forecast_wholesale;
    name: Wholesale
    unit: c/kWh
    color: cyan
```

### Forecast Only Chart

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

### Chart Data Attributes

| Sensor | Attribute | Description |
|--------|-----------|-------------|
| Import Price | `apex_import_history` | Historical import prices (up to 48h, `[[epoch_ms, cents], ...]`) |
| Price Forecast | `apex_forecast_import` | Forward curve of import prices inc. network tariff (c/kWh) |
| Price Forecast | `apex_forecast_wholesale` | Forward curve of raw wholesale prices (c/kWh) |

## Optimizer Integration

The `sensor.flow_power_<region>_price_forecast` sensor provides forecast attributes compatible with EMHASS and HAEO:

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

**V2 formula** (with network tariff configured):

```
PEA = GST × Wholesale + Network Tariff Rate - GST × TWAP - Avg Daily Tariff - BPEA
Final Rate = Base Rate + PEA
```

**Legacy formula** (without network tariff):

```
PEA = Wholesale - TWAP - BPEA
Final Rate = Base Rate + PEA
```

Where:
- TWAP = 30-day rolling average of raw wholesale spot prices (dynamic)
- BPEA = 1.7 c/kWh (Benchmark Price Efficiency Adjustment)
- Network Tariff Rate = Time-of-use network charge for the current half-hour period
- Avg Daily Tariff = 24-hour average of network tariff (nets to zero over a full day)
- Default Base Rate = 34.0 c/kWh
- When insufficient data (<1 hour), TWAP defaults to 8.0 c/kWh

Use `Base Rate` for the fixed Flow Power energy component from your plan/PDS. When network tariff support is enabled, the current TOU network swing is applied separately through `Network Tariff Rate - Avg Daily Tariff`, so that swing should not be baked into `Base Rate`.

The import price sensor includes the current network time-of-use swing used by the PEA formula:

```
Network TOU adjustment = Network Tariff Rate - Avg Daily Tariff
```

If the Flow Power app's **Price of energy** differs by roughly this amount, compare it with the sensor attributes `network_tou_adjustment_cents` and `price_without_network_tou_adjustment_cents`. The app display can omit or smooth the current network TOU adjustment, while the import price sensor is the current complete import estimate used for automation.

`price_without_network_tou_adjustment_cents` removes only the current network tariff swing. It still includes the same BPEA and GST inputs as the full import price, so it will not necessarily match the plain regional `sensor.flow_power_<region>_import_price` unless those account inputs happen to align.

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
