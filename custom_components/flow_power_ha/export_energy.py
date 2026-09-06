"""Validation and window calculations for optional HA export-energy counters."""
from __future__ import annotations

from math import isfinite
from typing import Any


def total_increasing_energy_value(state: Any) -> float | None:
    """Return a usable kWh total-increasing energy state, if it is one."""
    attributes = getattr(state, "attributes", {})
    if not isinstance(attributes, dict):
        return None
    if attributes.get("device_class") != "energy":
        return None
    if attributes.get("state_class") != "total_increasing":
        return None
    if attributes.get("unit_of_measurement") != "kWh":
        return None
    try:
        value = float(getattr(state, "state", None))
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) and value >= 0 else None


def usage_since_baseline(
    baseline_kwh: float | None,
    current_kwh: float | None,
) -> float | None:
    """Return an energy-counter delta, refusing counter resets or bad values."""
    if baseline_kwh is None or current_kwh is None:
        return None
    usage = current_kwh - baseline_kwh
    return round(usage, 4) if usage >= 0 else None
