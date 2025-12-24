"""Flow Power pricing calculations including PEA and export rates."""
from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .const import (
    FLOW_POWER_DEFAULT_BASE_RATE,
    FLOW_POWER_EXPORT_RATES,
    FLOW_POWER_PEA_OFFSET,
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
) -> dict[str, float]:
    """Calculate the final import price using Flow Power PEA formula.

    Final Rate = Base Rate + PEA
    Where PEA = Wholesale - 9.7 (or custom value if provided)

    The base_rate should be entered as it appears in the PDS (GST inclusive,
    with network charges already built in).

    Args:
        wholesale_cents: Wholesale price in c/kWh
        base_rate: Flow Power base rate in c/kWh (default 34.0, GST inclusive)
        pea_enabled: Whether to apply PEA calculation
        pea_custom_value: Optional fixed PEA override in c/kWh

    Returns:
        Dict with price breakdown:
        {
            'final_cents': 32.5,      # Final price in c/kWh
            'final_dollars': 0.325,   # Final price in $/kWh
            'base_rate': 34.0,        # Base rate in c/kWh
            'pea': -1.5,              # PEA adjustment in c/kWh
            'wholesale': 8.2,         # Wholesale in c/kWh
        }
    """
    result = {
        "wholesale": wholesale_cents,
        "base_rate": base_rate,
        "pea": 0.0,
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
    else:
        # Just base rate
        final_cents = base_rate

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
) -> list[dict[str, Any]]:
    """Calculate import prices for a forecast array.

    Args:
        forecast_data: List of forecast periods with wholesale prices
        base_rate: Flow Power base rate in c/kWh (GST inclusive)
        pea_enabled: Whether to apply PEA calculation
        pea_custom_value: Optional fixed PEA override in c/kWh

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
