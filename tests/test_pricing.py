from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components" / "flow_power_ha"

package = types.ModuleType("flow_power_ha")
package.__path__ = [str(COMPONENT_ROOT)]
sys.modules.setdefault("flow_power_ha", package)

from flow_power_ha.pricing import calculate_import_price  # noqa: E402
from flow_power_ha.tariff_utils import _dispatch_interval_end  # noqa: E402


def test_import_price_exposes_network_tou_adjustment() -> None:
    price = calculate_import_price(
        wholesale_cents=20.0,
        base_rate=34.0,
        twap=10.0,
        network_tariff_rate=5.6,
        avg_daily_tariff=1.1,
    )

    assert price["final_cents"] == 47.8
    assert price["network_tou_adjustment"] == 4.5
    assert price["price_without_network_tou_adjustment_cents"] == 43.3
    assert price["price_without_network_tou_adjustment_dollars"] == 0.433


def test_dispatch_interval_end_uses_next_five_minute_boundary() -> None:
    assert _dispatch_interval_end(
        datetime(2026, 5, 27, 10, 0, 5, 123456, tzinfo=timezone.utc)
    ) == datetime(2026, 5, 27, 10, 5, tzinfo=timezone.utc)

    assert _dispatch_interval_end(
        datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 5, 27, 10, 5, tzinfo=timezone.utc)

    assert _dispatch_interval_end(
        datetime(2026, 5, 27, 9, 59, 59, 999999, tzinfo=timezone.utc)
    ) == datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)
