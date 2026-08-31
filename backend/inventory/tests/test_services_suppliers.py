"""Tests for :mod:`inventory.services.suppliers` — single-primary enforcement and
PriceHistory recording extracted from ``ItemSupplier.save()`` (gh #887, AC-2).

PriceHistory had zero test coverage before this change; these tests exercise
both the extracted service functions directly and their behaviour through
``ItemSupplier.save()`` so the create/update history rows and the
one-primary-per-item guarantee stay locked in.
"""

from decimal import Decimal

import pytest

from inventory.models import ItemSupplier, PriceHistory
from inventory.services.suppliers import (
    enforce_single_primary,
    pricing_changed,
    record_price_history,
    write_supplier_terms,
)
from inventory.tests.factories import (
    InventoryItemFactory,
    ItemSupplierFactory,
    SupplierFactory,
)

pytestmark = pytest.mark.django_db


class TestEnforceSinglePrimaryViaSave:
    """Saving a primary supplier demotes the item's other primaries — same item only."""

    def test_creating_second_primary_demotes_first(self):
        item = InventoryItemFactory(image=None)
        first = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=True)
        second = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=True)

        first.refresh_from_db()
        assert second.is_primary is True
        assert first.is_primary is False

    def test_non_primary_save_leaves_existing_primary(self):
        item = InventoryItemFactory(image=None)
        primary = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=True)
        ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=False)

        primary.refresh_from_db()
        assert primary.is_primary is True

    def test_scoped_to_same_item(self):
        item_a = InventoryItemFactory(image=None)
        item_b = InventoryItemFactory(image=None)
        primary_a = ItemSupplierFactory(item=item_a, supplier=SupplierFactory(), is_primary=True)
        ItemSupplierFactory(item=item_b, supplier=SupplierFactory(), is_primary=True)

        primary_a.refresh_from_db()
        assert primary_a.is_primary is True


class TestEnforceSinglePrimaryService:
    """enforce_single_primary called directly."""

    def test_demotes_sibling_primaries(self):
        item = InventoryItemFactory(image=None)
        sibling = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=True)
        # A second row that is primary in memory but not yet enforced.
        newcomer = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=False)
        newcomer.is_primary = True

        enforce_single_primary(newcomer)

        sibling.refresh_from_db()
        assert sibling.is_primary is False

    def test_noop_when_not_primary(self):
        item = InventoryItemFactory(image=None)
        primary = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=True)
        other = ItemSupplierFactory(item=item, supplier=SupplierFactory(), is_primary=False)

        enforce_single_primary(other)

        primary.refresh_from_db()
        assert primary.is_primary is True


class TestPriceHistoryViaSave:
    """PriceHistory is written on create and on a real pricing change — never otherwise."""

    def test_create_writes_created_history(self):
        link = ItemSupplierFactory(package_cost=Decimal("12.00"), quantity_per_package=1)

        history = PriceHistory.objects.filter(item_supplier=link)
        assert history.count() == 1
        assert history.first().change_type == PriceHistory.ChangeType.CREATED

    def test_price_change_writes_updated_history(self):
        link = ItemSupplierFactory(package_cost=Decimal("12.00"), quantity_per_package=1)

        link.package_cost = Decimal("24.00")
        link.save()

        history = PriceHistory.objects.filter(item_supplier=link).order_by("recorded_at")
        assert history.count() == 2
        assert history.last().change_type == PriceHistory.ChangeType.UPDATED
        # unit_cost is derived from package_cost / quantity_per_package (24.00 / 1).
        assert history.last().unit_cost == Decimal("24.00")

    def test_quantity_change_writes_updated_history(self):
        link = ItemSupplierFactory(package_cost=Decimal("12.00"), quantity_per_package=1)

        link.quantity_per_package = 2
        link.save()

        history = PriceHistory.objects.filter(item_supplier=link)
        assert history.count() == 2

    def test_non_pricing_save_writes_no_new_history(self):
        link = ItemSupplierFactory(package_cost=Decimal("12.00"), quantity_per_package=1)

        link.notes = "just a note"
        link.save()

        assert PriceHistory.objects.filter(item_supplier=link).count() == 1


class TestPriceHistoryService:
    """pricing_changed / record_price_history called directly."""

    def test_pricing_changed_false_for_unsaved(self):
        item = InventoryItemFactory(image=None)
        link = ItemSupplier(
            item=item,
            supplier=SupplierFactory(),
            supplier_sku="X",
            unit_cost=Decimal("1.00"),
            package_cost=None,
            quantity_per_package=1,
        )
        assert pricing_changed(link) is False

    def test_pricing_changed_detects_change(self):
        link = ItemSupplierFactory(package_cost=Decimal("10.00"), quantity_per_package=1)
        link.package_cost = Decimal("20.00")
        assert pricing_changed(link) is True

    def test_pricing_changed_false_when_unchanged(self):
        link = ItemSupplierFactory(package_cost=Decimal("10.00"), quantity_per_package=1)
        assert pricing_changed(link) is False

    def test_record_price_history_noop_when_unchanged(self):
        link = ItemSupplierFactory(package_cost=Decimal("10.00"), quantity_per_package=1)
        before = PriceHistory.objects.filter(item_supplier=link).count()

        result = record_price_history(link, is_new=False, price_changed=False)

        assert result is None
        assert PriceHistory.objects.filter(item_supplier=link).count() == before

    def test_record_price_history_writes_updated_row(self):
        link = ItemSupplierFactory(package_cost=Decimal("10.00"), quantity_per_package=1)
        before = PriceHistory.objects.filter(item_supplier=link).count()

        row = record_price_history(link, is_new=False, price_changed=True)

        assert row is not None
        assert row.change_type == PriceHistory.ChangeType.UPDATED
        assert PriceHistory.objects.filter(item_supplier=link).count() == before + 1


@pytest.mark.django_db(transaction=True)
def test_a_create_that_is_not_the_race_reports_its_own_error():
    """An unknown supplier fails loudly out of the owner, as an IntegrityError.

    What this holds is the failure MODE, not the retry branch: measured, Django
    creates the foreign key `DEFERRABLE INITIALLY DEFERRED`, so the violation
    surfaces at COMMIT rather than at the INSERT and never enters the
    losing-create `except` at all. The retry's narrowing is precision for an
    IMMEDIATE constraint that is not the unique-together race; no reachable
    caller produces one today, so nothing here pins that branch.
    """
    from django.db import IntegrityError

    item = InventoryItemFactory()

    with pytest.raises(IntegrityError):
        write_supplier_terms(item=item, supplier_id=999999, unit_cost=Decimal("4.00"))

    assert not ItemSupplier.objects.filter(supplier_id=999999).exists()
