"""Tests for the serialized-component consumption forecast + low-stock report.

Covers the mode-aware depletion logic in
``inventory.services.component_forecast`` and the
``InventoryReportViewSet.serialized_forecast`` endpoint that exposes it.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
from rest_framework import status

from inventory.models import ComponentUsageEvent, InventoryItem, SerializedComponent
from inventory.services.component_forecast import DEFAULT_WINDOW_DAYS, build_component_forecast
from inventory.tests.factories import InventoryItemFactory

User = get_user_model()

pytestmark = pytest.mark.django_db

FORECAST_URL = "/api/inventory/reports/inventory/serialized_forecast/"


def _serialized_item(mode, **kwargs):
    return InventoryItemFactory(is_serialized=True, serial_tracking_mode=mode, **kwargs)


def _components(item, status_value, n, prefix):
    """Create ``n`` components of ``item`` pinned to ``status_value``."""
    return [
        SerializedComponent.objects.create(
            item=item, serial_number=f"{prefix}-{i}", status=status_value
        )
        for i in range(n)
    ]


def _event(component, action, days_ago, now):
    return ComponentUsageEvent.objects.create(
        component=component, action=action, at=now - timedelta(days=days_ago)
    )


def _row_for(rows, item):
    return next(r for r in rows if r["item_id"] == str(item.id))


def _add_lead_time_log(item, user, actual_days):
    """Attach an observed LeadTimeLog of ``actual_days`` to the item's supplier."""
    from reorder_queue.models import LeadTimeLog, PurchaseOrder

    item_supplier = item.item_suppliers.first()
    po = PurchaseOrder.objects.create(supplier=item_supplier.supplier, created_by=user)
    return LeadTimeLog.objects.create(
        item_supplier=item_supplier,
        purchase_order=po,
        order_date=timezone.now() - timedelta(days=actual_days + 5),
        expected_delivery_date=(timezone.now() - timedelta(days=5)).date(),
        actual_delivery_date=timezone.now().date(),
        estimated_lead_time_days=actual_days,
        actual_lead_time_days=actual_days,
        quantity_ordered=10,
        quantity_received=10,
    )


@pytest.mark.unit
class TestBuildComponentForecast:
    def test_consumable_depletes_on_consume_not_receive_or_install(self):
        """Only ``consume`` counts as depletion for consumable items. An
        installed unit stays *on hand* (physically present) but is no longer
        *available* to install; the depletion rate ignores receive/install."""
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)

        # On hand: 6 on the shelf + 1 installed (not yet consumed) = 7.
        _components(item, SerializedComponent.IN_STOCK, 6, "stock")
        installed = _components(item, SerializedComponent.INSTALLED, 1, "inst")

        # Depleted: 4 consumed units, each with a consume event inside the window.
        consumed = _components(item, SerializedComponent.CONSUMED, 4, "used")
        for i, comp in enumerate(consumed):
            _event(comp, SerializedComponent.ACTION_CONSUME, days_ago=i + 1, now=now)

        # Noise the rate must ignore: receive/install events in the window.
        _event(installed[0], SerializedComponent.ACTION_RECEIVE, days_ago=3, now=now)
        _event(installed[0], SerializedComponent.ACTION_INSTALL, days_ago=2, now=now)

        row = _row_for(build_component_forecast(now=now), item)

        assert row["on_hand"] == 7
        assert row["installed"] == 1
        assert row["available"] == 6  # on_hand minus the 1 installed
        assert row["available_stock"] == 7  # back-compat alias of on_hand
        assert row["units_depleted_in_window"] == 4
        assert row["avg_daily_use"] == round(4 / DEFAULT_WINDOW_DAYS, 4)

    def test_reusable_depletes_on_retire_dispose_not_reuse(self):
        """Reusable install/remove cycling does not deplete; only retire/dispose
        does. Installed and removed units are both *on hand*, but only removed
        (and in-stock) units are *available* — installed ones are not."""
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_REUSABLE, minimum_stock=0)

        # On hand: 5 in stock + 2 installed + 1 removed = 8 (reuse states count).
        in_stock = _components(item, SerializedComponent.IN_STOCK, 5, "stock")
        installed = _components(item, SerializedComponent.INSTALLED, 2, "inst")
        removed = _components(item, SerializedComponent.REMOVED, 1, "rem")

        # Depleted: 3 retired units with retire events in the window.
        retired = _components(item, SerializedComponent.RETIRED, 3, "ret")
        for i, comp in enumerate(retired):
            _event(comp, SerializedComponent.ACTION_RETIRE, days_ago=i + 1, now=now)

        # Reuse-cycle noise that must NOT be counted as depletion.
        _event(installed[0], SerializedComponent.ACTION_INSTALL, days_ago=4, now=now)
        _event(removed[0], SerializedComponent.ACTION_REMOVE, days_ago=3, now=now)
        _event(in_stock[0], SerializedComponent.ACTION_RECEIVE, days_ago=2, now=now)

        row = _row_for(build_component_forecast(now=now), item)

        assert row["on_hand"] == 8
        assert row["installed"] == 2
        assert row["available"] == 6  # 8 on_hand - 2 installed; removed stays available
        assert row["available_stock"] == 8  # back-compat alias of on_hand
        assert row["units_depleted_in_window"] == 3
        assert row["avg_daily_use"] == round(3 / DEFAULT_WINDOW_DAYS, 4)

    def test_reusable_retire_then_dispose_counts_once(self):
        """A reusable unit retired *and* disposed inside the window is one
        depletion, not two."""
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_REUSABLE, minimum_stock=0)
        _components(item, SerializedComponent.IN_STOCK, 2, "stock")

        retired = _components(item, SerializedComponent.RETIRED, 2, "ret")
        for i, comp in enumerate(retired):
            _event(comp, SerializedComponent.ACTION_RETIRE, days_ago=i + 1, now=now)

        # One unit that has both a retire and a dispose event within the window.
        disposed = _components(item, SerializedComponent.DISPOSED, 1, "disp")[0]
        _event(disposed, SerializedComponent.ACTION_RETIRE, days_ago=6, now=now)
        _event(disposed, SerializedComponent.ACTION_DISPOSE, days_ago=5, now=now)

        row = _row_for(build_component_forecast(now=now), item)

        # 2 retired-only + 1 retired-and-disposed = 3 distinct depleted units.
        assert row["units_depleted_in_window"] == 3

    def test_forecast_math_days_until_stockout_and_reorder_point(self):
        now = timezone.now()
        user = User.objects.create_user(username="lt-user", password="x")
        item = _serialized_item(
            InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=2, current_stock=10
        )
        _add_lead_time_log(item, user, actual_days=10)

        _components(item, SerializedComponent.IN_STOCK, 10, "stock")  # available = 10
        consumed = _components(item, SerializedComponent.CONSUMED, 9, "used")
        for i, comp in enumerate(consumed):
            _event(comp, SerializedComponent.ACTION_CONSUME, days_ago=i + 1, now=now)

        row = _row_for(build_component_forecast(now=now), item)

        assert row["available_stock"] == 10
        assert row["current_stock"] == 10
        # 9 depletions over 90 days -> 0.1/day -> 10 / 0.1 = 100 days.
        assert row["avg_daily_use"] == 0.1
        assert row["days_until_stockout"] == 100.0
        assert row["lead_time_days"] == 10.0
        # reorder_point = ceil(0.1 * 10 + safety(minimum_stock=2)) = 3.
        assert row["reorder_point"] == 3
        assert row["safety_stock"] == 2
        assert row["needs_reorder"] is False
        expected_date = (now + timedelta(days=100)).date().isoformat()
        assert row["projected_stockout_date"] == expected_date

    def test_low_stock_only_filters_to_reorder_candidates(self):
        now = timezone.now()
        user = User.objects.create_user(username="ls-user", password="x")

        low = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=5)
        _add_lead_time_log(low, user, actual_days=10)
        _components(low, SerializedComponent.IN_STOCK, 2, "low")  # available = 2
        for i, comp in enumerate(_components(low, SerializedComponent.CONSUMED, 18, "lu")):
            _event(comp, SerializedComponent.ACTION_CONSUME, days_ago=(i % 80) + 1, now=now)

        healthy = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)
        _components(healthy, SerializedComponent.IN_STOCK, 50, "hs")

        all_rows = build_component_forecast(now=now)
        low_row = _row_for(all_rows, low)
        # 18/90 = 0.2/day, available 2 -> 10 days; reorder_point ceil(0.2*10+5)=7 >= 2.
        assert low_row["avg_daily_use"] == 0.2
        assert low_row["days_until_stockout"] == 10.0
        assert low_row["needs_reorder"] is True
        assert _row_for(all_rows, healthy)["needs_reorder"] is False

        filtered = build_component_forecast(now=now, low_stock_only=True)
        ids = {r["item_id"] for r in filtered}
        assert str(low.id) in ids
        assert str(healthy.id) not in ids

    def test_lead_time_falls_back_to_supplier_estimate(self):
        now = timezone.now()
        item = _serialized_item(
            InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0, average_lead_time=7
        )
        _components(item, SerializedComponent.IN_STOCK, 3, "stock")

        row = _row_for(build_component_forecast(now=now), item)
        assert row["lead_time_days"] == 7.0

    def test_zero_depletion_has_no_stockout_date(self):
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=4)
        _components(item, SerializedComponent.IN_STOCK, 3, "stock")

        row = _row_for(build_component_forecast(now=now), item)
        assert row["avg_daily_use"] == 0.0
        assert row["days_until_stockout"] is None
        assert row["projected_stockout_date"] is None
        # With no usage, reorder_point is just the safety stock; 3 <= 4 -> reorder.
        assert row["reorder_point"] == 4
        assert row["needs_reorder"] is True

    def test_events_outside_window_are_ignored(self):
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)
        _components(item, SerializedComponent.IN_STOCK, 5, "stock")
        old = _components(item, SerializedComponent.CONSUMED, 3, "old")
        for i, comp in enumerate(old):
            _event(comp, SerializedComponent.ACTION_CONSUME, days_ago=200 + i, now=now)

        row = _row_for(build_component_forecast(now=now, window_days=90), item)
        assert row["units_depleted_in_window"] == 0
        assert row["avg_daily_use"] == 0.0

    def test_window_days_param_changes_rate(self):
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)
        _components(item, SerializedComponent.IN_STOCK, 10, "stock")
        consumed = _components(item, SerializedComponent.CONSUMED, 10, "used")
        for i, comp in enumerate(consumed):
            _event(comp, SerializedComponent.ACTION_CONSUME, days_ago=i + 1, now=now)

        row = _row_for(build_component_forecast(now=now, window_days=10), item)
        # All 10 consume events fall within the tighter 10-day window.
        assert row["avg_daily_use"] == 1.0
        assert row["days_until_stockout"] == 10.0

    def test_excludes_non_serialized_and_inactive_items(self):
        now = timezone.now()
        serialized = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE)
        _components(serialized, SerializedComponent.IN_STOCK, 1, "stock")

        plain = InventoryItemFactory(is_serialized=False)
        inactive = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, is_active=False)

        ids = {r["item_id"] for r in build_component_forecast(now=now)}
        assert str(serialized.id) in ids
        assert str(plain.id) not in ids
        assert str(inactive.id) not in ids

    def test_install_lowers_available_but_not_on_hand(self):
        """Installing a consumable unit moves it out of ``available`` while it
        stays ``on_hand`` (physically present) until it is consumed."""
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)
        _components(item, SerializedComponent.IN_STOCK, 4, "stock")
        _components(item, SerializedComponent.INSTALLED, 3, "inst")

        row = _row_for(build_component_forecast(now=now), item)
        assert row["on_hand"] == 7  # 4 in_stock + 3 installed
        assert row["installed"] == 3
        assert row["available"] == 4  # installed excluded

    def test_consume_lowers_on_hand_and_available(self):
        """A consumed unit is depleted: it counts toward neither on_hand nor
        available (only the 2 in-stock units remain)."""
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)
        _components(item, SerializedComponent.IN_STOCK, 2, "stock")
        _components(item, SerializedComponent.CONSUMED, 5, "used")

        row = _row_for(build_component_forecast(now=now), item)
        assert row["on_hand"] == 2
        assert row["available"] == 2
        assert row["installed"] == 0

    def test_reusable_removed_unit_is_available(self):
        """A reusable unit that has been *removed* from its asset returns to the
        available pool, while installed units do not."""
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_REUSABLE, minimum_stock=0)
        _components(item, SerializedComponent.INSTALLED, 2, "inst")
        _components(item, SerializedComponent.REMOVED, 3, "rem")

        row = _row_for(build_component_forecast(now=now), item)
        assert row["on_hand"] == 5  # installed + removed are both present
        assert row["installed"] == 2
        assert row["available"] == 3  # removed counts as available; installed does not

    def test_installed_units_can_trigger_reorder(self):
        """The forecast math uses ``available``, so units that are installed
        (lowering available without lowering on_hand) can push an item to its
        reorder point even with no depletion history."""
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=3)
        _components(item, SerializedComponent.IN_STOCK, 2, "stock")
        _components(item, SerializedComponent.INSTALLED, 4, "inst")

        row = _row_for(build_component_forecast(now=now), item)
        assert row["on_hand"] == 6
        assert row["available"] == 2
        # No depletion -> reorder_point == safety_stock (minimum_stock) == 3.
        assert row["reorder_point"] == 3
        # available(2) <= 3 -> reorder; had the math used on_hand(6) it would not.
        assert row["needs_reorder"] is True

    def test_days_until_stockout_uses_available_not_on_hand(self):
        """Runway is measured against ``available``: installed units do not
        extend the projected stockout."""
        now = timezone.now()
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)
        _components(item, SerializedComponent.IN_STOCK, 5, "stock")
        _components(item, SerializedComponent.INSTALLED, 5, "inst")
        consumed = _components(item, SerializedComponent.CONSUMED, 9, "used")
        for i, comp in enumerate(consumed):
            _event(comp, SerializedComponent.ACTION_CONSUME, days_ago=i + 1, now=now)

        row = _row_for(build_component_forecast(now=now), item)
        assert row["on_hand"] == 10
        assert row["available"] == 5
        # 9 consume events / 90 days = 0.1/day; available 5 -> 50 days (not 100).
        assert row["avg_daily_use"] == 0.1
        assert row["days_until_stockout"] == 50.0


@pytest.mark.integration
class TestSerializedForecastEndpoint:
    def test_requires_authentication(self, api_client):
        assert api_client.get(FORECAST_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_forecast_rows(self, authenticated_client):
        client, _ = authenticated_client
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)
        _components(item, SerializedComponent.IN_STOCK, 4, "stock")
        consumed = _components(item, SerializedComponent.CONSUMED, 9, "used")
        now = timezone.now()
        for i, comp in enumerate(consumed):
            _event(comp, SerializedComponent.ACTION_CONSUME, days_ago=i + 1, now=now)

        response = client.get(FORECAST_URL)
        assert response.status_code == status.HTTP_200_OK
        row = _row_for(response.data, item)
        assert row["serial_tracking_mode"] == InventoryItem.SERIAL_TRACKING_CONSUMABLE
        assert row["available_stock"] == 4
        assert row["on_hand"] == 4
        assert row["available"] == 4
        assert row["installed"] == 0
        assert row["units_depleted_in_window"] == 9

    def test_low_stock_only_query_param(self, authenticated_client):
        client, _ = authenticated_client
        now = timezone.now()

        low = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=10)
        _components(low, SerializedComponent.IN_STOCK, 1, "low")
        for i, comp in enumerate(_components(low, SerializedComponent.CONSUMED, 5, "lu")):
            _event(comp, SerializedComponent.ACTION_CONSUME, days_ago=i + 1, now=now)

        healthy = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)
        _components(healthy, SerializedComponent.IN_STOCK, 100, "hs")

        response = client.get(FORECAST_URL, {"low_stock_only": "true"})
        assert response.status_code == status.HTTP_200_OK
        ids = {r["item_id"] for r in response.data}
        assert str(low.id) in ids
        assert str(healthy.id) not in ids

    def test_lifecycle_driven_usage_feeds_forecast(self, authenticated_client):
        """End-to-end: real lifecycle transitions (which write ComponentUsageEvent
        rows) drive the depletion rate."""
        client, _ = authenticated_client
        item = _serialized_item(InventoryItem.SERIAL_TRACKING_CONSUMABLE, minimum_stock=0)

        # One unit consumed via the real lifecycle API path.
        component = SerializedComponent.objects.create(item=item, serial_number="LC-1")
        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        # Keep a couple of spare units available on the shelf.
        _components(item, SerializedComponent.IN_STOCK, 3, "spare")

        response = client.get(FORECAST_URL)
        assert response.status_code == status.HTTP_200_OK
        row = _row_for(response.data, item)
        # The received unit is in stock alongside the 3 spares.
        assert row["available_stock"] == 4
        # A bare receive is not a depletion.
        assert row["units_depleted_in_window"] == 0
