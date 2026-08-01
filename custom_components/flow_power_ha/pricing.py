"""Flow Power pricing calculations including PEA and export rates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from .const import (
    CURRENT_EXPORT_PREMIUM_CAP_KWH,
    CURRENT_EXPORT_WINDOW_END,
    CURRENT_EXPORT_WINDOW_START,
    FLOW_POWER_BENCHMARK,
    FLOW_POWER_DEFAULT_BASE_RATE,
    FLOW_POWER_EXPORT_RATES,
    FLOW_POWER_GST,
    FLOW_POWER_MARKET_AVG,
    FLOW_HOME_EXPORT_RATES_CENTS,
    FOUR_FREE_IMPORT_END,
    FOUR_FREE_IMPORT_HOURLY_CAP_KWH,
    FOUR_FREE_IMPORT_START,
    FOUR_FREE_LOWER_EXPORT_RATES_CENTS,
    FOUR_FREE_PREMIUM_EXPORT_RATES_CENTS,
    HAPPY_HOUR_LOWER_EXPORT_RATE_CENTS,
    HAPPY_HOUR_PREMIUM_EXPORT_RATES_CENTS,
    HAPPY_HOUR_END,
    HAPPY_HOUR_START,
    PLAN_4FREE,
    PLAN_FLOW_HOME,
    PLAN_HAPPY_HOUR,
    PLAN_LEGACY_HAPPY_HOUR,
)
from .flow_power_pricing import FlowPowerPricingContext, calculate_flow_power_pea


REGION_TIMEZONES = {
    "NSW1": "Australia/Sydney",
    "QLD1": "Australia/Brisbane",
    "VIC1": "Australia/Melbourne",
    "SA1": "Australia/Adelaide",
    "TAS1": "Australia/Hobart",
}


@dataclass(frozen=True)
class RateQuote:
    """A conservative current or forecast tariff quote in cents/kWh."""

    rate: float
    rate_min: float
    rate_max: float
    is_exact: bool
    calculation_basis: str
    window_active: bool
    cap_limit_kwh: float | None
    cap_used_kwh: float | None
    cap_remaining_kwh: float | None
    cap_status: str
    uncertainty_reason: str | None


def _local_datetime(
    current_time: datetime | None,
    region: str,
    timezone: str | None = None,
) -> datetime:
    """Return a timestamp in the plan's local timezone."""
    tz = ZoneInfo(timezone or REGION_TIMEZONES.get(region, "Australia/Sydney"))
    if current_time is None:
        return datetime.now(tz)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=tz)
    return current_time.astimezone(tz)


def _known_usage(value: float | None) -> float | None:
    """Return a usable non-negative cumulative energy value."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _exact_quote(
    rate: float,
    *,
    window_active: bool,
    cap_limit_kwh: float | None = None,
    cap_used_kwh: float | None = None,
    cap_remaining_kwh: float | None = None,
    cap_status: str = "not_applicable",
) -> RateQuote:
    """Build an exact quote."""
    rounded = round(max(0.0, rate), 4)
    return RateQuote(
        rate=rounded,
        rate_min=rounded,
        rate_max=rounded,
        is_exact=True,
        calculation_basis="exact",
        window_active=window_active,
        cap_limit_kwh=cap_limit_kwh,
        cap_used_kwh=cap_used_kwh,
        cap_remaining_kwh=cap_remaining_kwh,
        cap_status=cap_status,
        uncertainty_reason=None,
    )


def quote_import_rate(
    *,
    plan: str,
    region: str,
    gross_rate: float,
    current_time: datetime | None = None,
    timezone: str | None = None,
    imported_this_hour_kwh: float | None = None,
) -> RateQuote:
    """Quote the effective import rate without inventing 4Free allowance state."""
    gross = round(max(0.0, float(gross_rate)), 4)
    local_dt = _local_datetime(current_time, region, timezone)
    in_free_window = FOUR_FREE_IMPORT_START <= local_dt.time() < FOUR_FREE_IMPORT_END

    if plan != PLAN_4FREE:
        return _exact_quote(gross, window_active=False)
    if not in_free_window:
        return _exact_quote(
            gross,
            window_active=False,
            cap_limit_kwh=FOUR_FREE_IMPORT_HOURLY_CAP_KWH,
            cap_status="not_active",
        )

    used = _known_usage(imported_this_hour_kwh)
    if used is None:
        return RateQuote(
            rate=gross,
            rate_min=0.0,
            rate_max=gross,
            is_exact=False,
            calculation_basis="conservative_maximum",
            window_active=True,
            cap_limit_kwh=FOUR_FREE_IMPORT_HOURLY_CAP_KWH,
            cap_used_kwh=None,
            cap_remaining_kwh=None,
            cap_status="unknown",
            uncertainty_reason="hourly_import_usage_unavailable",
        )

    remaining = round(max(0.0, FOUR_FREE_IMPORT_HOURLY_CAP_KWH - used), 4)
    rate = 0.0 if used < FOUR_FREE_IMPORT_HOURLY_CAP_KWH else gross
    return _exact_quote(
        rate,
        window_active=True,
        cap_limit_kwh=FOUR_FREE_IMPORT_HOURLY_CAP_KWH,
        cap_used_kwh=round(used, 4),
        cap_remaining_kwh=remaining,
        cap_status="known",
    )


def quote_export_rate(
    *,
    plan: str,
    region: str,
    current_time: datetime | None = None,
    timezone: str | None = None,
    legacy_override_rate: float | None = None,
    exported_this_window_kwh: float | None = None,
) -> RateQuote:
    """Quote an export rate, using the guaranteed tier when cap usage is unknown."""
    local_dt = _local_datetime(current_time, region, timezone)
    local_time = local_dt.time()

    if plan == PLAN_FLOW_HOME:
        return _exact_quote(
            FLOW_HOME_EXPORT_RATES_CENTS.get(region, 0.0),
            window_active=True,
        )

    if plan == PLAN_LEGACY_HAPPY_HOUR:
        active = HAPPY_HOUR_START <= local_time < HAPPY_HOUR_END
        legacy_rate = (
            float(legacy_override_rate) * 100
            if legacy_override_rate is not None
            else FLOW_POWER_EXPORT_RATES.get(region, 0.0) * 100
        )
        return _exact_quote(legacy_rate if active else 0.0, window_active=active)

    active = CURRENT_EXPORT_WINDOW_START <= local_time < CURRENT_EXPORT_WINDOW_END
    if plan == PLAN_HAPPY_HOUR:
        premium = HAPPY_HOUR_PREMIUM_EXPORT_RATES_CENTS.get(region, 0.0)
        lower = HAPPY_HOUR_LOWER_EXPORT_RATE_CENTS if premium > 0 else 0.0
    elif plan == PLAN_4FREE:
        premium = FOUR_FREE_PREMIUM_EXPORT_RATES_CENTS.get(region, 0.0)
        lower = FOUR_FREE_LOWER_EXPORT_RATES_CENTS.get(region, 0.0)
    else:
        return _exact_quote(0.0, window_active=False)

    if not active:
        return _exact_quote(
            0.0,
            window_active=False,
            cap_limit_kwh=CURRENT_EXPORT_PREMIUM_CAP_KWH,
            cap_status="not_active",
        )

    used = _known_usage(exported_this_window_kwh)
    if used is None:
        return RateQuote(
            rate=round(lower, 4),
            rate_min=round(lower, 4),
            rate_max=round(premium, 4),
            is_exact=lower == premium,
            calculation_basis=(
                "exact" if lower == premium else "conservative_minimum"
            ),
            window_active=True,
            cap_limit_kwh=CURRENT_EXPORT_PREMIUM_CAP_KWH,
            cap_used_kwh=None,
            cap_remaining_kwh=None,
            cap_status="unknown" if lower != premium else "not_applicable",
            uncertainty_reason=(
                None if lower == premium else "window_export_usage_unavailable"
            ),
        )

    remaining = round(max(0.0, CURRENT_EXPORT_PREMIUM_CAP_KWH - used), 4)
    rate = premium if used < CURRENT_EXPORT_PREMIUM_CAP_KWH else lower
    return _exact_quote(
        rate,
        window_active=True,
        cap_limit_kwh=CURRENT_EXPORT_PREMIUM_CAP_KWH,
        cap_used_kwh=round(used, 4),
        cap_remaining_kwh=remaining,
        cap_status="known",
    )


def calculate_pea(
    wholesale_cents: float,
    twap: float | None = None,
    network_tariff_rate: float | None = None,
    avg_daily_tariff: float | None = None,
    pricing_context: FlowPowerPricingContext | None = None,
) -> float:
    """Calculate the Price Efficiency Adjustment (PEA).

    Legacy formula (when network tariff params not provided):
        PEA = Wholesale - TWAP - BPEA

    V2 formula (when both network tariff params provided):
        PEA = GST * Wholesale + network_tariff_rate - GST * TWAP - avg_daily_tariff - BPEA

    Where:
        TWAP = Time Weighted Average Price (dynamic 30-day rolling average,
               or default 8.0 c/kWh when insufficient data)
        BPEA = Benchmark Price Efficiency Adjustment (1.7 c/kWh)
        GST = 1.1 (10% Goods and Services Tax)
        network_tariff_rate = current TOU network tariff rate in c/kWh
        avg_daily_tariff = 24h average network tariff in c/kWh

    Args:
        wholesale_cents: Wholesale price in c/kWh
        twap: Dynamic TWAP in c/kWh, or None to use default (8.0)
        network_tariff_rate: Current TOU network tariff rate in c/kWh, or None
        avg_daily_tariff: 24h average network tariff in c/kWh, or None

    Returns:
        PEA value in c/kWh (can be negative)
    """
    if pricing_context is not None:
        return calculate_flow_power_pea(
            wholesale_cents,
            pricing_context,
            tariff_rate=network_tariff_rate,
            avg_daily_tariff=avg_daily_tariff,
        )

    market_avg = twap if twap is not None else FLOW_POWER_MARKET_AVG

    if network_tariff_rate is not None and avg_daily_tariff is not None:
        # V2 formula with network tariff support
        return (
            FLOW_POWER_GST * wholesale_cents
            + network_tariff_rate
            - FLOW_POWER_GST * market_avg
            - avg_daily_tariff
            - FLOW_POWER_BENCHMARK
        )

    # Legacy formula
    return wholesale_cents - market_avg - FLOW_POWER_BENCHMARK


def calculate_import_price(
    wholesale_cents: float,
    base_rate: float = FLOW_POWER_DEFAULT_BASE_RATE,
    pea_enabled: bool = True,
    pea_custom_value: float | None = None,
    twap: float | None = None,
    network_tariff_rate: float | None = None,
    avg_daily_tariff: float | None = None,
    pricing_context: FlowPowerPricingContext | None = None,
    plan: str = PLAN_LEGACY_HAPPY_HOUR,
    region: str = "NSW1",
    current_time: datetime | None = None,
    timezone: str | None = None,
    imported_this_hour_kwh: float | None = None,
) -> dict[str, Any]:
    """Calculate the final import price using Flow Power PEA formula.

    Final Rate = Base Rate + PEA
    Where PEA = Wholesale - TWAP - BPEA (legacy)
    Or PEA = GST*Wholesale + network_tariff - GST*TWAP - avg_tariff - BPEA (V2)

    The base_rate should be entered as it appears in the PDS (GST inclusive,
    with network charges already built in).

    Args:
        wholesale_cents: Wholesale price in c/kWh
        base_rate: Flow Power base rate in c/kWh (default 34.0, GST inclusive)
        pea_enabled: Whether to apply PEA calculation
        pea_custom_value: Optional fixed PEA override in c/kWh
        twap: Dynamic TWAP in c/kWh, or None to use default (8.0)
        network_tariff_rate: Current TOU network tariff rate in c/kWh, or None
        avg_daily_tariff: 24h average network tariff in c/kWh, or None

    Returns:
        Dict with price breakdown:
        {
            'final_cents': 32.5,      # Final price in c/kWh
            'final_dollars': 0.325,   # Final price in $/kWh
            'base_rate': 34.0,        # Base rate in c/kWh
            'pea': -1.5,             # PEA adjustment in c/kWh
            'wholesale': 8.2,         # Wholesale in c/kWh
            'twap_used': 7.5,        # TWAP value used in calculation
            'network_tariff_rate': 5.0,  # Network tariff rate (None if not provided)
            'avg_daily_tariff': 4.2,     # Avg daily tariff (None if not provided)
        }
    """
    twap_used = (
        pricing_context.twap
        if pricing_context is not None
        else twap if twap is not None else FLOW_POWER_MARKET_AVG
    )

    result: dict[str, Any] = {
        "wholesale": wholesale_cents,
        "base_rate": base_rate,
        "pea": 0.0,
        "twap_used": twap_used,
        "network_tariff_rate": network_tariff_rate,
        "avg_daily_tariff": avg_daily_tariff,
        "network_tou_adjustment": None,
        "price_without_network_tou_adjustment_cents": None,
        "price_without_network_tou_adjustment_dollars": None,
        "twap_source": pricing_context.twap_source if pricing_context else (
            "dynamic" if twap is not None else "fallback"
        ),
        "bpea": pricing_context.bpea if pricing_context else FLOW_POWER_BENCHMARK,
        "bpea_source": pricing_context.bpea_source if pricing_context else "default",
        "gst_multiplier": pricing_context.gst_multiplier if pricing_context else FLOW_POWER_GST,
        "gst_source": pricing_context.gst_source if pricing_context else "default",
        "account_pricing_active": pricing_context.account_data_active if pricing_context else False,
        "final_cents": 0.0,
        "final_dollars": 0.0,
    }

    if pea_enabled:
        # Use custom PEA if provided, otherwise calculate with dynamic TWAP
        if pea_custom_value is not None:
            pea = pea_custom_value
        else:
            pea = calculate_pea(
                wholesale_cents,
                twap=twap,
                network_tariff_rate=network_tariff_rate,
                avg_daily_tariff=avg_daily_tariff,
                pricing_context=pricing_context,
            )

        result["pea"] = pea
        raw_final_cents = base_rate + pea
    else:
        # Just base rate
        raw_final_cents = base_rate

    if network_tariff_rate is not None and avg_daily_tariff is not None:
        network_tou_adjustment = network_tariff_rate - avg_daily_tariff
        result["network_tou_adjustment"] = round(network_tou_adjustment, 4)
        without_network_tou = max(0.0, raw_final_cents - network_tou_adjustment)
        result["price_without_network_tou_adjustment_cents"] = round(
            without_network_tou, 2
        )
        result["price_without_network_tou_adjustment_dollars"] = round(
            without_network_tou / 100, 4
        )

    # Ensure non-negative (Tesla restriction), then apply the selected plan's
    # effective-rate overlay. For an untracked 4Free allowance the numeric state
    # remains the conservative maximum and the possible zero rate is exposed in
    # the quote range.
    gross_final_cents = max(0.0, raw_final_cents)
    quote = quote_import_rate(
        plan=plan,
        region=region,
        gross_rate=gross_final_cents,
        current_time=current_time,
        timezone=timezone,
        imported_this_hour_kwh=imported_this_hour_kwh,
    )

    result["plan"] = plan
    result["gross_final_cents"] = round(gross_final_cents, 2)
    result["gross_final_dollars"] = round(gross_final_cents / 100, 4)
    result["final_cents"] = round(quote.rate, 2)
    result["final_dollars"] = round(quote.rate / 100, 4)
    result.update({
        "rate_min_cents": round(quote.rate_min, 2),
        "rate_max_cents": round(quote.rate_max, 2),
        "rate_min_dollars": round(quote.rate_min / 100, 4),
        "rate_max_dollars": round(quote.rate_max / 100, 4),
        "rate_is_exact": quote.is_exact,
        "calculation_basis": quote.calculation_basis,
        "window_active": quote.window_active,
        "cap_limit_kwh": quote.cap_limit_kwh,
        "cap_used_kwh": quote.cap_used_kwh,
        "cap_remaining_kwh": quote.cap_remaining_kwh,
        "cap_status": quote.cap_status,
        "uncertainty_reason": quote.uncertainty_reason,
    })

    return result


def calculate_export_price(
    region: str,
    current_time: datetime | None = None,
    timezone: str | None = None,
    happy_hour_rate_override: float | None = None,
    plan: str = PLAN_LEGACY_HAPPY_HOUR,
    exported_this_window_kwh: float | None = None,
) -> dict[str, Any]:
    """Calculate a conservative plan-aware export price for the region."""
    quote = quote_export_rate(
        plan=plan,
        region=region,
        current_time=current_time,
        timezone=timezone,
        legacy_override_rate=happy_hour_rate_override,
        exported_this_window_kwh=exported_this_window_kwh,
    )

    if plan == PLAN_LEGACY_HAPPY_HOUR:
        window_start = HAPPY_HOUR_START
        window_end = HAPPY_HOUR_END
    elif plan in (PLAN_HAPPY_HOUR, PLAN_4FREE):
        window_start = CURRENT_EXPORT_WINDOW_START
        window_end = CURRENT_EXPORT_WINDOW_END
    else:
        window_start = None
        window_end = None

    return {
        "plan": plan,
        "export_cents": round(quote.rate, 2),
        "export_dollars": round(quote.rate / 100, 4),
        "is_happy_hour": quote.window_active,
        "happy_hour_rate": round(quote.rate_max / 100, 4),
        "region": region,
        "happy_hour_start": window_start.strftime("%H:%M") if window_start else None,
        "happy_hour_end": window_end.strftime("%H:%M") if window_end else None,
        "rate_min_cents": round(quote.rate_min, 2),
        "rate_max_cents": round(quote.rate_max, 2),
        "rate_min_dollars": round(quote.rate_min / 100, 4),
        "rate_max_dollars": round(quote.rate_max / 100, 4),
        "rate_is_exact": quote.is_exact,
        "calculation_basis": quote.calculation_basis,
        "window_active": quote.window_active,
        "cap_limit_kwh": quote.cap_limit_kwh,
        "cap_used_kwh": quote.cap_used_kwh,
        "cap_remaining_kwh": quote.cap_remaining_kwh,
        "cap_status": quote.cap_status,
        "uncertainty_reason": quote.uncertainty_reason,
    }


def calculate_forecast_prices(
    forecast_data: list[dict[str, Any]],
    base_rate: float = FLOW_POWER_DEFAULT_BASE_RATE,
    pea_enabled: bool = True,
    pea_custom_value: float | None = None,
    twap: float | None = None,
    tariff_schedule: dict[int, float] | None = None,
    avg_daily_tariff: float | None = None,
    pricing_context: FlowPowerPricingContext | None = None,
    plan: str = PLAN_LEGACY_HAPPY_HOUR,
    region: str = "NSW1",
) -> list[dict[str, Any]]:
    """Calculate import prices for a forecast array.

    Args:
        forecast_data: List of forecast periods with wholesale prices
        base_rate: Flow Power base rate in c/kWh (GST inclusive)
        pea_enabled: Whether to apply PEA calculation
        pea_custom_value: Optional fixed PEA override in c/kWh
        twap: Dynamic TWAP in c/kWh, or None to use default
        tariff_schedule: Maps half-hour slot index (0-47) to tariff rate in c/kWh,
                         or None to skip network tariff in PEA
        avg_daily_tariff: 24h average network tariff in c/kWh, or None

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
        # Extract wholesale price (AEMO format: c/kWh)
        if "perKwh" in period:
            wholesale_cents = period["perKwh"]
        else:
            continue

        # Forecast timestamps identify the end of the interval. Plan windows and
        # tariff schedules both apply to the interval start.
        timestamp = period.get("nemTime") or period.get("startTime") or ""
        interval_minutes = int(period.get("duration", 30) or 30)
        interval_start: datetime | None = None
        if timestamp:
            try:
                interval_start = datetime.fromisoformat(timestamp.replace("/", "-"))
                interval_start -= timedelta(minutes=interval_minutes)
            except (ValueError, TypeError):
                interval_start = None
        if interval_start is None:
            # An interval with no usable time cannot be placed into a plan or
            # network-tariff window without inventing a result.
            continue

        # Determine per-period network tariff rate from schedule
        network_tariff_rate: float | None = None
        if tariff_schedule is not None and interval_start is not None:
            slot_index = interval_start.hour * 2 + interval_start.minute // 30
            network_tariff_rate = tariff_schedule.get(slot_index)

        # Calculate final price
        price_info = calculate_import_price(
            wholesale_cents=wholesale_cents,
            base_rate=base_rate,
            pea_enabled=pea_enabled,
            pea_custom_value=pea_custom_value,
            twap=twap,
            network_tariff_rate=network_tariff_rate,
            avg_daily_tariff=avg_daily_tariff,
            pricing_context=pricing_context,
            plan=plan,
            region=region,
            current_time=interval_start,
        )

        results.append({
            "timestamp": timestamp,
            "duration_minutes": interval_minutes,
            "price_dollars": price_info["final_dollars"],
            "price_cents": price_info["final_cents"],
            "gross_price_dollars": price_info["gross_final_dollars"],
            "gross_price_cents": price_info["gross_final_cents"],
            "plan": price_info["plan"],
            "rate_min_dollars": price_info["rate_min_dollars"],
            "rate_max_dollars": price_info["rate_max_dollars"],
            "rate_min_cents": price_info["rate_min_cents"],
            "rate_max_cents": price_info["rate_max_cents"],
            "rate_is_exact": price_info["rate_is_exact"],
            "calculation_basis": price_info["calculation_basis"],
            "window_active": price_info["window_active"],
            "cap_limit_kwh": price_info["cap_limit_kwh"],
            "cap_used_kwh": price_info["cap_used_kwh"],
            "cap_remaining_kwh": price_info["cap_remaining_kwh"],
            "cap_status": price_info["cap_status"],
            "uncertainty_reason": price_info["uncertainty_reason"],
            "wholesale_cents": wholesale_cents,
            "pea": price_info["pea"],
            "network_tariff_rate": network_tariff_rate,
            "twap_used": price_info["twap_used"],
            "twap_source": price_info["twap_source"],
            "bpea": price_info["bpea"],
            "bpea_source": price_info["bpea_source"],
            "gst_multiplier": price_info["gst_multiplier"],
        })

    return results
