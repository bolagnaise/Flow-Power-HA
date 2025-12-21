"""Constants for Flow Power HA integration."""
from datetime import time

DOMAIN = "flow_power_ha"

# PEA (Price Efficiency Adjustment) Constants
FLOW_POWER_MARKET_AVG = 8.0  # Market TWAP average (c/kWh)
FLOW_POWER_BENCHMARK = 1.7  # BPEA - benchmark customer performance (c/kWh)
FLOW_POWER_PEA_OFFSET = 9.7  # Combined: MARKET_AVG + BENCHMARK (c/kWh)
FLOW_POWER_DEFAULT_BASE_RATE = 34.0  # Default Flow Power base rate (c/kWh)

# NEM Regions
NEM_REGIONS = {
    "NSW1": "New South Wales",
    "QLD1": "Queensland",
    "VIC1": "Victoria",
    "SA1": "South Australia",
    "TAS1": "Tasmania",
}

# Export Rates by Region (Happy Hour rates in $/kWh)
FLOW_POWER_EXPORT_RATES = {
    "NSW1": 0.45,  # 45c/kWh
    "QLD1": 0.45,  # 45c/kWh
    "SA1": 0.45,   # 45c/kWh
    "VIC1": 0.35,  # 35c/kWh
    "TAS1": 0.00,  # No Happy Hour in Tasmania
}

# Happy Hour Time Window (local time)
HAPPY_HOUR_START = time(17, 30)  # 5:30 PM
HAPPY_HOUR_END = time(19, 30)    # 7:30 PM

# Price Sources
PRICE_SOURCE_AMBER = "amber"
PRICE_SOURCE_AEMO = "aemo"

# Configuration Keys
CONF_PRICE_SOURCE = "price_source"
CONF_AMBER_API_KEY = "amber_api_key"
CONF_AMBER_SITE_ID = "amber_site_id"
CONF_NEM_REGION = "nem_region"
CONF_BASE_RATE = "base_rate"
CONF_PEA_ENABLED = "pea_enabled"
CONF_PEA_CUSTOM_VALUE = "pea_custom_value"
CONF_INCLUDE_NETWORK_TARIFF = "include_network_tariff"
CONF_NETWORK_FLAT_RATE = "network_flat_rate"
CONF_OTHER_FEES = "other_fees"
CONF_INCLUDE_GST = "include_gst"

# Default Configuration Values
DEFAULT_BASE_RATE = 34.0
DEFAULT_NETWORK_FLAT_RATE = 8.0
DEFAULT_OTHER_FEES = 1.5
DEFAULT_INCLUDE_GST = True

# Update Intervals (seconds)
UPDATE_INTERVAL_CURRENT = 300  # 5 minutes for current prices
UPDATE_INTERVAL_FORECAST = 1800  # 30 minutes for forecasts

# Sensor Types
SENSOR_TYPE_IMPORT_PRICE = "import_price"
SENSOR_TYPE_EXPORT_PRICE = "export_price"
SENSOR_TYPE_WHOLESALE_PRICE = "wholesale_price"
SENSOR_TYPE_PRICE_FORECAST = "price_forecast"

# API URLs
AEMO_CURRENT_PRICE_URL = "https://visualisations.aemo.com.au/aemo/apps/api/report/ELEC_NEM_SUMMARY"
AEMO_5MIN_PREDISPATCH_URL = "https://visualisations.aemo.com.au/aemo/apps/api/report/5MIN_PREDISPATCH"
AEMO_PREDISPATCH_PRICES_URL = "https://visualisations.aemo.com.au/aemo/apps/api/report/PREDISPATCH_PRICES"
AEMO_FORECAST_BASE_URL = "https://nemweb.com.au/Reports/Current/Predispatch_Reports/"
AMBER_API_BASE_URL = "https://api.amber.com.au/v1"

# GST Rate (Australia)
GST_RATE = 0.10  # 10%
