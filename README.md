# Flow Power HA

A Home Assistant integration that provides Flow Power electricity pricing sensors for EMHASS optimization.

## Features

- **Price Sources**: Supports both Amber Electric and AEMO (direct wholesale) as price data sources
- **PEA Calculation**: Implements Flow Power's Price Efficiency Adjustment formula
- **Happy Hour Export**: Automatic export pricing based on Flow Power Happy Hour (5:30pm-7:30pm)
- **EMHASS Compatible**: Price forecast sensor with attributes for EMHASS integration
- **Configurable**: Base rates, network tariffs, and GST options

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

### Pricing Settings

| Option | Default | Description |
|--------|---------|-------------|
| Base Rate | 34.0 c/kWh | Your Flow Power base energy rate |
| PEA Enabled | Yes | Apply Price Efficiency Adjustment |
| PEA Custom Value | - | Override calculated PEA with fixed value |
| Include Network Tariff | No | Add network charges (AEMO mode) |
| Network Rate | 8.0 c/kWh | Flat network usage charge |
| Other Fees | 1.5 c/kWh | Environmental and market fees |
| Include GST | Yes | Add 10% GST to final price |

## Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| `sensor.flow_power_import_price` | $/kWh | Current import price with PEA |
| `sensor.flow_power_export_price` | $/kWh | Current export price (Happy Hour aware) |
| `sensor.flow_power_wholesale_price` | c/kWh | Raw wholesale price |
| `sensor.flow_power_price_forecast` | $/kWh | Price forecast for EMHASS |

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
PEA = Wholesale (c/kWh) - 9.7
Final Rate = Base Rate + PEA

Where:
- 9.7 = Market Average (8.0) + Benchmark (1.7)
- Default Base Rate = 34.0 c/kWh
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

For issues and feature requests, please open an issue on GitHub.

## License

MIT License
