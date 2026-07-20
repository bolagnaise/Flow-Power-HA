"""Constants for Flow Power HA integration."""
from datetime import time

DOMAIN = "flow_power_ha"

# PEA (Price Efficiency Adjustment) Constants
FLOW_POWER_MARKET_AVG = 8.0  # Default TWAP fallback when insufficient data (c/kWh)
FLOW_POWER_BENCHMARK = 1.7  # BPEA - benchmark customer performance (c/kWh)
FLOW_POWER_DEFAULT_BASE_RATE = 34.0  # Default Flow Power base rate (c/kWh)

# NEM Regions
NEM_REGIONS = {
    "NSW1": "New South Wales",
    "QLD1": "Queensland",
    "VIC1": "Victoria",
    "SA1": "South Australia",
    "TAS1": "Tasmania",
}

# GST multiplier
FLOW_POWER_GST = 1.1

# Network tariff configuration keys
CONF_FP_NETWORK = "fp_network"
CONF_FP_TARIFF_CODE = "fp_tariff_code"
CONF_FP_TWAP_OVERRIDE = "fp_twap_override"

# NEM region → list of DNSP display names
REGION_NETWORKS = {
    "NSW1": ["Ausgrid", "Endeavour", "Essential"],
    "QLD1": ["Energex", "Ergon"],
    "VIC1": ["Powercor", "CitiPower", "AusNet", "Jemena", "United"],
    "SA1": ["SAPN"],
    "TAS1": ["TasNetworks"],
}

# Display name → aemo_to_tariff network parameter (for spot_to_tariff() calls)
NETWORK_API_NAME = {
    "Ausgrid": "ausgrid",
    "Endeavour": "endeavour",
    "Essential": "essential",
    "Energex": "energex",
    "Ergon": "ergon",
    "SAPN": "sapn",
    "Powercor": "powercor",
    "CitiPower": "victoria",
    "AusNet": "ausnet",
    "Jemena": "jemena",
    "United": "victoria",
    "TasNetworks": "tasnetworks",
    "Evoenergy": "evoenergy",
}

# Display name → aemo_to_tariff module name (for importlib imports)
NETWORK_MODULE_NAME = {
    "Ausgrid": "ausgrid",
    "Endeavour": "endeavour",
    "Essential": "essential",
    "Energex": "energex",
    "Ergon": "ergon",
    "SAPN": "sapower",
    "Powercor": "powercor",
    "CitiPower": "victoria",
    "AusNet": "ausnet",
    "Jemena": "jemena",
    "United": "victoria",
    "TasNetworks": "tasnetworks",
    "Evoenergy": "evoenergy",
}

# Display name → tariff lookup URL for each DNSP
NETWORK_TARIFF_URL = {
    "Ausgrid": "https://www.ausgrid.com.au/Your-energy-use/Meters/Tariffs-on-your-meter",
    "Endeavour": "https://www.endeavourenergy.com.au/your-energy/understand-your-energy/network-prices",
    "Essential": "https://www.essentialenergy.com.au/our-network/network-pricing",
    "Energex": "https://www.energex.com.au/home/our-services/pricing-And-tariffs/residential-tariffs",
    "Ergon": "https://www.ergon.com.au/network/network-management/network-tariffs",
    "SAPN": "https://www.sapowernetworks.com.au/industry/pricing/current-network-prices/",
    "Powercor": "https://www.powercor.com.au/industry/pricing-and-tariffs/network-tariff-rates/",
    "CitiPower": "https://www.powercor.com.au/industry/pricing-and-tariffs/network-tariff-rates/",
    "United": "https://www.powercor.com.au/industry/pricing-and-tariffs/network-tariff-rates/",
    "AusNet": "https://www.ausnetservices.com.au/about/network-prices/electricity-distribution-prices",
    "Jemena": "https://jemena.com.au/price-and-availability/electricity-prices",
    "TasNetworks": "https://www.tasnetworks.com.au/config/getattachment/3d6ca9fb-b3d2-464e-9d90-dfe26ae84c8e/tariff-schedule.pdf",
    "Evoenergy": "https://www.evoenergy.com.au/residents/understanding-electricity-pricing",
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
PRICE_SOURCE_AEMO = "aemo"
PRICE_SOURCE_FLOWPOWER = "flowpower"

# Flow Power KWatch API
FLOWPOWER_API_BASE_URL = "https://api.kwatch.com.au/api/v1"
FLOWPOWER_KWATCH_REGIONS = {
    "NSW1": "nsw",
    "QLD1": "qld",
    "VIC1": "vic",
    "SA1": "sa",
    "TAS1": "tas",
}

# Configuration Keys
CONF_PRICE_SOURCE = "price_source"
CONF_NEM_REGION = "nem_region"
CONF_BASE_RATE = "base_rate"
CONF_PEA_ENABLED = "pea_enabled"
CONF_PEA_CUSTOM_VALUE = "pea_custom_value"
CONF_HAPPY_HOUR_EXPORT_RATE = "happy_hour_export_rate"

# Flow Power Web Data API configuration keys
CONF_FLOWPOWER_API_KEY = "flowpower_api_key"
CONF_FLOWPOWER_NMI = "flowpower_nmi"

# Default Configuration Values
DEFAULT_BASE_RATE = 34.0

# Update Intervals (seconds)
UPDATE_INTERVAL_CURRENT = 300  # 5 minutes for current prices
UPDATE_INTERVAL_FORECAST = 1800  # 30 minutes for forecasts

# Sensor Types
SENSOR_TYPE_IMPORT_PRICE = "import_price"
SENSOR_TYPE_EXPORT_PRICE = "export_price"
SENSOR_TYPE_WHOLESALE_PRICE = "wholesale_price"
SENSOR_TYPE_PRICE_FORECAST = "price_forecast"
SENSOR_TYPE_TWAP = "twap"
SENSOR_TYPE_FLOWPOWER_ACCOUNT = "flowpower_account"
SENSOR_TYPE_NETWORK_TARIFF = "network_tariff"

# API account sensors — (sensor_type, name, data_key, unit, icon, source_label)
# source_label: "api" = direct from Flow Power, "calculated" = derived from API data
ACCOUNT_SENSORS = [
    # PEA metrics
    ("account_pea", "Account PEA (Actual)", "pea_actual", "c/kWh", "mdi:account-cash", "api"),
    ("account_pea_30d", "Account PEA 30-Day", "pea_30_days", "c/kWh", "mdi:calendar-month", "api"),
    ("account_bpea", "Account BPEA (Benchmark)", "bpea", "c/kWh", "mdi:target", "api"),
    ("account_cpea", "Account CPEA (Customer)", "cpea", "c/kWh", "mdi:account-arrow-right", "calculated"),
    ("account_pea_import", "Account PEA Import", "pea_actual_import", "c/kWh", "mdi:import", "api"),
    # Weighted average prices
    ("account_lwap", "Account LWAP", "lwap", "c/kWh", "mdi:scale-balance", "api"),
    ("account_lwap_actual", "Account LWAP (Actual)", "lwap_actual", "c/kWh", "mdi:scale-balance", "api"),
    ("account_twap", "Account TWAP", "twap", "c/kWh", "mdi:chart-timeline-variant", "api"),
    ("account_avg_rrp", "Account Avg Spot Price", "avg_rrp", "c/kWh", "mdi:lightning-bolt", "api"),
    # Site factors
    ("account_dlf", "Account DLF (Site Losses)", "site_losses_dlf", None, "mdi:transmission-tower", "api"),
    # Usage metrics
    ("account_avg_usage", "Account Avg Demand", "avg_usage_kw", "kW", "mdi:flash-outline", "api"),
    ("account_max_usage", "Account Max Demand", "max_usage_kw", "kW", "mdi:flash-alert", "api"),
]

# TWAP (Time Weighted Average Price) Settings
DEFAULT_TWAP_WINDOW_DAYS = 30  # Rolling window for TWAP calculation
MIN_TWAP_SAMPLES = 12  # Minimum samples (~1 hour) before using dynamic TWAP

# API URLs
# Legacy JSON API (slower)
AEMO_CURRENT_PRICE_URL = "https://visualisations.aemo.com.au/aemo/apps/api/report/ELEC_NEM_SUMMARY"
AEMO_5MIN_PREDISPATCH_URL = "https://visualisations.aemo.com.au/aemo/apps/api/report/5MIN_PREDISPATCH"
AEMO_PREDISPATCH_PRICES_URL = "https://visualisations.aemo.com.au/aemo/apps/api/report/PREDISPATCH_PRICES"

# NEMWEB ZIP endpoints (faster - raw source data)
AEMO_DISPATCH_URL = "https://nemweb.com.au/Reports/Current/DispatchIS_Reports/"
AEMO_FORECAST_BASE_URL = "https://nemweb.com.au/Reports/Current/Predispatch_Reports/"

# Flow Power account API update interval (account data changes slowly)
UPDATE_INTERVAL_FLOWPOWER = 1800  # 30 minutes
# after login (they may be account-specific)
