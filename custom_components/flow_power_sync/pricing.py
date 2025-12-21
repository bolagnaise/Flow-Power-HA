"""Flow Power pricing calculations including PEA and export rates."""
from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .const import (
    DEFAULT_BASE_RATE,
    DEFAULT_NETWORK_FLAT_RATE,
    DEFAULT_OTHER_FEES,
    FLOW_POWER_DEFAULT_BASE_RATE,
    FLOW_POWER_EXPORT_RATES,
    FLOW_POWER_PEA_OFFSET,
    GST_RATE,
    HAPPY_HOUR_END,
    HAPPY_HOUR_START,
)


def calculate_pea(wholesale_cents: float) -> float:
    """Calculate the Price Efficiency Adjustment (PEA).

    PEA = Wholesale (c/kWh) - 9.7 (Market Avg + Benchmark)

    Args:
        wholesale_cents: Wholesale price in c/kWh

    Returns:
        PEA value in c/kWh (can be negative)
    """
    return wholesale_cents - FLOW_POWER_PEA_OFFSET


def calculate_import_price(
    wholesale_cents: float,
    base_rate: float = FLOW_POWER_DEFAULT_BASE_RATE,
    pea_enabled: bool = True,
    pea_custom_value: float | None = None,
    include_network_tariff: bool = False,
    network_flat_rate: float = DEFAULT_NETWORK_FLAT_RATE,
    other_fees: float = DEFAULT_OTHER_FEES,
    include_gst: bool = True,
) -> dict[str, float]:
    """Calculate the final import price using Flow Power PEA formula.

    Final Rate = Base Rate + PEA
    Where PEA = Wholesale - 9.7 (or custom value if provided)

    Args:
        wholesale_cents: Wholesale price in c/kWh
        base_rate: Flow Power base rate in c/kWh (default 34.0)
        pea_enabled: Whether to apply PEA calculation
        pea_custom_value: Optional fixed PEA override in c/kWh
        include_network_tariff: Whether to add network tariff (for AEMO mode)
        network_flat_rate: Network charge in c/kWh
        other_fees: Environmental/market fees in c/kWh
        include_gst: Whether to include 10% GST

    Returns:
        Dict with price breakdown:
        {
            'final_cents': 32.5,      # Final price in c/kWh
            'final_dollars': 0.325,   # Final price in $/kWh
            'base_rate': 34.0,        # Base rate in c/kWh
            'pea': -1.5,              # PEA adjustment in c/kWh
            'wholesale': 8.2,         # Wholesale in c/kWh
            'network': 0.0,           # Network charge in c/kWh
            'other_fees': 0.0,        # Other fees in c/kWh
            'gst': 0.0,               # GST amount in c/kWh
        }
    """
    result = {
        "wholesale": wholesale_cents,
        "base_rate": base_rate,
        "pea": 0.0,
        "network": 0.0,
        "other_fees": 0.0,
        "gst": 0.0,
        "final_cents": 0.0,
        "final_dollars": 0.0,
    }

    if pea_enabled:
        # Use custom PEA if provided, otherwise calculate
        if pea_custom_value is not None:
            pea = pea_custom_value
        else:
            pea = calculate_pea(wholesale_cents)

        result["pea"] = pea
        final_cents = base_rate + pea
    elif include_network_tariff:
        # AEMO mode without PEA: wholesale + network + fees
        result["network"] = network_flat_rate
        result["other_fees"] = other_fees
        final_cents = wholesale_cents + network_flat_rate + other_fees
    else:
        # Just base rate
        final_cents = base_rate

    # Apply GST if enabled
    if include_gst and include_network_tariff:
        gst_amount = final_cents * GST_RATE
        result["gst"] = gst_amount
        final_cents += gst_amount

    # Ensure non-negative (Tesla restriction)
    final_cents = max(0.0, final_cents)

    result["final_cents"] = round(final_cents, 2)
    result["final_dollars"] = round(final_cents / 100, 4)

    return result


def calculate_export_price(
    region: str,
    current_time: datetime | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    """Calculate the export price based on Happy Hour and region.

    Happy Hour: 5:30pm - 7:30pm local time
    Rates: NSW1/QLD1/SA1 = 45c, VIC1 = 35c, others = 0c

    Args:
        region: NEM region code (NSW1, QLD1, VIC1, SA1, TAS1)
        current_time: Optional datetime for testing (defaults to now)
        timezone: Optional timezone string (defaults based on region)

    Returns:
        Dict with export price info:
        {
            'export_cents': 45.0,      # Export price in c/kWh
            'export_dollars': 0.45,    # Export price in $/kWh
            'is_happy_hour': True,     # Whether currently in Happy Hour
            'happy_hour_rate': 0.45,   # Happy Hour rate for region
            'region': 'NSW1',
        }
    """
    # Determine timezone
    if timezone is None:
        timezone_map = {
            "NSW1": "Australia/Sydney",
            "QLD1": "Australia/Brisbane",
            "VIC1": "Australia/Melbourne",
            "SA1": "Australia/Adelaide",
            "TAS1": "Australia/Hobart",
        }
        timezone = timezone_map.get(region, "Australia/Sydney")

    # Get current time in local timezone
    tz = ZoneInfo(timezone)
    if current_time is None:
        current_time = datetime.now(tz)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=tz)

    local_time = current_time.astimezone(tz).time()

    # Check if in Happy Hour window
    is_happy_hour = HAPPY_HOUR_START <= local_time < HAPPY_HOUR_END

    # Get Happy Hour rate for region
    happy_hour_rate = FLOW_POWER_EXPORT_RATES.get(region, 0.0)

    # Calculate export price
    if is_happy_hour:
        export_cents = happy_hour_rate * 100  # Convert $/kWh to c/kWh
    else:
        export_cents = 0.0

    return {
        "export_cents": export_cents,
        "export_dollars": export_cents / 100,
        "is_happy_hour": is_happy_hour,
        "happy_hour_rate": happy_hour_rate,
        "region": region,
        "happy_hour_start": HAPPY_HOUR_START.strftime("%H:%M"),
        "happy_hour_end": HAPPY_HOUR_END.strftime("%H:%M"),
    }


def calculate_forecast_prices(
    forecast_data: list[dict[str, Any]],
    base_rate: float = FLOW_POWER_DEFAULT_BASE_RATE,
    pea_enabled: bool = True,
    pea_custom_value: float | None = None,
    include_network_tariff: bool = False,
    network_flat_rate: float = DEFAULT_NETWORK_FLAT_RATE,
    other_fees: float = DEFAULT_OTHER_FEES,
    include_gst: bool = True,
) -> list[dict[str, Any]]:
    """Calculate import prices for a forecast array.

    Args:
        forecast_data: List of forecast periods with wholesale prices
        Other args same as calculate_import_price

    Returns:
        List of forecast periods with calculated prices:
        [
            {
                'timestamp': '2024-01-01T00:00:00+10:00',
                'price_dollars': 0.325,
                'price_cents': 32.5,
                'wholesale_cents': 8.2,
            },
            ...
        ]
    """
    results = []

    for period in forecast_data:
        # Extract wholesale price (handle both Amber and AEMO formats)
        if "wholesaleKWHPrice" in period:
            # Amber format: $/kWh
            wholesale_cents = period["wholesaleKWHPrice"] * 100
        elif "perKwh" in period:
            # AEMO format: c/kWh
            wholesale_cents = period["perKwh"]
        else:
            continue

        # Calculate final price
        price_info = calculate_import_price(
            wholesale_cents=wholesale_cents,
            base_rate=base_rate,
            pea_enabled=pea_enabled,
            pea_custom_value=pea_custom_value,
            include_network_tariff=include_network_tariff,
            network_flat_rate=network_flat_rate,
            other_fees=other_fees,
            include_gst=include_gst,
        )

        # Extract timestamp
        timestamp = period.get("nemTime") or period.get("startTime") or ""

        results.append({
            "timestamp": timestamp,
            "price_dollars": price_info["final_dollars"],
            "price_cents": price_info["final_cents"],
            "wholesale_cents": wholesale_cents,
            "pea": price_info["pea"],
        })

    return results
