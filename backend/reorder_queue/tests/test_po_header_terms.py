"""Editable PO header + derived payment schedule (op-bwo9).

A purchase order's header carries more than a supplier and a status:

* ``order_date`` is now **user-editable**. It used to be ``auto_now_add``, which
  stamped "now" and refused every later correction — wrong for an order typed up
  after the phone call that placed it. It defaults to now and can be backdated.
* ``priority`` (low/normal/high/urgent, defaults to normal), ``payment_terms``
  and ``freight_terms`` (both optional) describe the deal. All three are plain
  metadata: nothing here moves stock or posts to the ledger.
* ``payment_schedule`` derives the single payment the terms imply —
  ``{due_date, amount, basis}`` — from fields the order already carries. It is
  computed on every read, never stored, so the web create-form can mirror the
  same math client-side before the order exists.

What this file pins down:

* the defaults and the exact choice sets;
* ``order_date`` is writable at create *and* on PATCH, accepts a backdate, and
  rejects a date a year out; the ordering and ``days_since_ordered`` behaviours
  that read it are unchanged;
* the due-date rule for every payment term, including the two that anchor to
  delivery and the blank one that anchors to nothing;
* ``amount`` tracks the *live* estimated total, so voiding a line moves it.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.tests.factories import ItemSupplierFactory, SupplierFactory
from reorder_queue.admin import PurchaseOrderAdmin
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem
from reorder_queue.serializers import PurchaseOrderSerializer
from reorder_queue.tests.factories import UserFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _staff():
    return User.objects.create_user(username="buyer", password="x", is_staff=True)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _po(user=None, **kwargs):
    return PurchaseOrder.objects.create(
        supplier=kwargs.pop("supplier", None) or SupplierFactory(),
        status=kwargs.pop("status", PurchaseOrder.Status.DRAFT),
        created_by=user or _staff(),
        **kwargs,
    )


def _line(po, **kwargs):
    """One inventory line on ``po``. quantity_per_package=1 pins the unit cost."""
    item_supplier = kwargs.pop("item_supplier", None) or ItemSupplierFactory(
        supplier=po.supplier, quantity_per_package=1
    )
    return PurchaseOrderItem.objects.create(
        purchase_order=po,
        item_supplier=item_supplier,
        quantity_ordered=kwargs.pop("quantity_ordered", 5),
        unit_cost_ordered=kwargs.pop("unit_cost_ordered", Decimal("2.00")),
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Defaults and choices
# ─────────────────────────────────────────────────────────────────────────────
def test_new_order_defaults_to_normal_priority_and_no_agreed_terms():
    po = _po()

    assert po.priority == PurchaseOrder.Priority.NORMAL
    assert po.payment_terms == ""
    assert po.freight_terms == ""


def test_priority_offers_four_levels_and_is_never_blank():
    assert [value for value, _ in PurchaseOrder.Priority.choices] == [
        "low",
        "normal",
        "high",
        "urgent",
    ]
    assert PurchaseOrder._meta.get_field("priority").blank is False


def test_payment_and_freight_terms_offer_their_sets_and_may_be_left_blank():
    assert [value for value, _ in PurchaseOrder.PaymentTerms.choices] == [
        "due_on_receipt",
        "net_15",
        "net_30",
        "net_60",
        "cod",
        "prepaid",
    ]
    assert [value for value, _ in PurchaseOrder.FreightTerms.choices] == [
        "fob_origin",
        "fob_destination",
        "prepaid",
        "collect",
        "third_party",
    ]
    assert PurchaseOrder._meta.get_field("payment_terms").blank is True
    assert PurchaseOrder._meta.get_field("freight_terms").blank is True


# ─────────────────────────────────────────────────────────────────────────────
# order_date is a real, editable field
# ─────────────────────────────────────────────────────────────────────────────
def test_order_date_defaults_to_now_but_is_not_stamped_automatically():
    """The whole point: no ``auto_now_add``, so a supplied value survives."""
    field = PurchaseOrder._meta.get_field("order_date")

    assert field.auto_now_add is False
    assert field.editable is True
    assert timezone.now() - _po().order_date < timedelta(minutes=1)


def test_order_can_be_backdated_to_when_it_was_actually_placed():
    placed = timezone.now() - timedelta(days=30)

    po = _po(order_date=placed)

    po.refresh_from_db()
    assert po.order_date == placed


def test_saving_an_order_again_does_not_restamp_its_order_date():
    placed = timezone.now() - timedelta(days=10)
    po = _po(order_date=placed)

    po.status = PurchaseOrder.Status.SENT
    po.save()

    po.refresh_from_db()
    assert po.order_date == placed


def test_orders_still_list_newest_first_by_order_date():
    """Meta.ordering=['-order_date'] keeps working on hand-entered dates."""
    user = _staff()
    old = _po(user, order_date=timezone.now() - timedelta(days=40))
    middle = _po(user, order_date=timezone.now() - timedelta(days=5))
    new = _po(user, order_date=timezone.now())

    assert list(PurchaseOrder.objects.all()) == [new, middle, old]


def test_days_since_ordered_still_counts_from_sent_at_not_order_date():
    """Backdating the header must not inflate the "how long has it been out" clock."""
    po = _po(order_date=timezone.now() - timedelta(days=90))
    assert po.days_since_ordered == 0

    po.sent_at = timezone.now() - timedelta(days=3)
    assert po.days_since_ordered == 3


# ─────────────────────────────────────────────────────────────────────────────
# Read serializer
# ─────────────────────────────────────────────────────────────────────────────
def test_serializer_exposes_the_header_fields_and_no_longer_freezes_order_date():
    po = _po(
        priority=PurchaseOrder.Priority.URGENT,
        payment_terms=PurchaseOrder.PaymentTerms.NET_30,
        freight_terms=PurchaseOrder.FreightTerms.FOB_DESTINATION,
    )

    data = PurchaseOrderSerializer(po).data

    assert data["priority"] == "urgent"
    assert data["payment_terms"] == "net_30"
    assert data["freight_terms"] == "fob_destination"
    assert "order_date" not in PurchaseOrderSerializer.Meta.read_only_fields
    assert PurchaseOrderSerializer().fields["order_date"].read_only is False


# ─────────────────────────────────────────────────────────────────────────────
# Derived payment schedule
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "terms,net_days,basis",
    [
        (PurchaseOrder.PaymentTerms.NET_15, 15, "Net 15 from order date"),
        (PurchaseOrder.PaymentTerms.NET_30, 30, "Net 30 from order date"),
        (PurchaseOrder.PaymentTerms.NET_60, 60, "Net 60 from order date"),
    ],
)
def test_net_terms_fall_due_that_many_days_after_the_order_date(terms, net_days, basis):
    placed = timezone.now() - timedelta(days=7)
    po = _po(order_date=placed, payment_terms=terms)

    schedule = po.payment_schedule

    assert schedule["due_date"] == placed.date() + timedelta(days=net_days)
    assert schedule["basis"] == basis


def test_prepaid_falls_due_on_the_order_date_itself():
    placed = timezone.now() - timedelta(days=2)
    po = _po(order_date=placed, payment_terms=PurchaseOrder.PaymentTerms.PREPAID)

    schedule = po.payment_schedule

    assert schedule["due_date"] == placed.date()
    assert schedule["basis"] == "Prepaid"


@pytest.mark.parametrize(
    "terms",
    [PurchaseOrder.PaymentTerms.DUE_ON_RECEIPT, PurchaseOrder.PaymentTerms.COD],
)
def test_delivery_anchored_terms_fall_due_on_the_expected_delivery_date(terms):
    expected = timezone.now().date() + timedelta(days=12)
    po = _po(payment_terms=terms, expected_delivery_date=expected)

    schedule = po.payment_schedule

    assert schedule["due_date"] == expected
    assert schedule["basis"] == "On delivery"


@pytest.mark.parametrize(
    "terms",
    [PurchaseOrder.PaymentTerms.DUE_ON_RECEIPT, PurchaseOrder.PaymentTerms.COD],
)
def test_delivery_anchored_terms_have_no_due_date_until_delivery_is_expected(terms):
    """ "On delivery" with no delivery date yet is a known rule, not a date."""
    po = _po(payment_terms=terms, expected_delivery_date=None)

    schedule = po.payment_schedule

    assert schedule["due_date"] is None
    assert schedule["basis"] == "On delivery"


def test_no_agreed_payment_terms_means_no_due_date():
    po = _po(expected_delivery_date=timezone.now().date() + timedelta(days=3))

    schedule = po.payment_schedule

    assert schedule["due_date"] is None
    assert schedule["basis"] == "No payment terms set"


def test_the_amount_due_is_the_orders_live_estimated_total():
    po = _po(payment_terms=PurchaseOrder.PaymentTerms.NET_30)
    _line(po, quantity_ordered=5, unit_cost_ordered=Decimal("2.00"))
    _line(po, quantity_ordered=10, unit_cost_ordered=Decimal("3.00"))
    po.calculate_estimated_total()
    po.save()

    assert po.payment_schedule["amount"] == Decimal("40.00")


def test_voiding_a_line_moves_the_payment_down_with_it():
    """``amount`` reuses effective_estimated_total, which excludes voided lines."""
    po = _po(payment_terms=PurchaseOrder.PaymentTerms.NET_30)
    _line(po, quantity_ordered=5, unit_cost_ordered=Decimal("2.00"))
    voided = _line(po, quantity_ordered=10, unit_cost_ordered=Decimal("3.00"))
    po.calculate_estimated_total()
    po.save()

    voided.is_voided = True
    voided.save()
    po = PurchaseOrder.objects.get(pk=po.pk)

    assert po.payment_schedule["amount"] == Decimal("10.00")


def test_api_renders_the_schedule_alongside_the_total_it_derives_from():
    placed = timezone.now() - timedelta(days=1)
    po = _po(order_date=placed, payment_terms=PurchaseOrder.PaymentTerms.NET_30)
    _line(po, quantity_ordered=5, unit_cost_ordered=Decimal("2.00"))
    po.calculate_estimated_total()
    po.save()

    data = PurchaseOrderSerializer(po).data

    assert data["payment_schedule"] == {
        "due_date": (placed.date() + timedelta(days=30)).isoformat(),
        "amount": "10.00",
        "basis": "Net 30 from order date",
    }
    # Same shape as the ``estimated_total`` it is derived from.
    assert data["payment_schedule"]["amount"] == data["estimated_total"]


def test_payment_schedule_is_derived_not_stored():
    """No new column: it is recomputed from the order's own fields on every read."""
    assert PurchaseOrderSerializer().fields["payment_schedule"].read_only is True
    assert "payment_schedule" not in {f.name for f in PurchaseOrder._meta.get_fields()}


# ─────────────────────────────────────────────────────────────────────────────
# Create API
# ─────────────────────────────────────────────────────────────────────────────
def test_create_accepts_a_backdated_order_date_and_the_three_terms():
    client = _client(_staff())
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)
    placed = timezone.now() - timedelta(days=14)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "order_date": placed.isoformat(),
            "priority": "urgent",
            "payment_terms": "net_15",
            "freight_terms": "fob_origin",
            "items": [{"item_supplier_id": item_supplier.id, "quantity": 3, "unit_cost": "1.50"}],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    po = PurchaseOrder.objects.get(id=response.data["id"])
    assert po.order_date == placed
    assert po.priority == "urgent"
    assert po.payment_terms == "net_15"
    assert po.freight_terms == "fob_origin"
    assert response.data["payment_schedule"] == {
        "due_date": (placed.date() + timedelta(days=15)).isoformat(),
        "amount": "4.50",
        "basis": "Net 15 from order date",
    }


def test_create_without_a_header_falls_back_to_now_and_normal_priority():
    client = _client(_staff())
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "items": [{"item_supplier_id": item_supplier.id, "quantity": 1, "unit_cost": "1.00"}],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    po = PurchaseOrder.objects.get(id=response.data["id"])
    assert timezone.now() - po.order_date < timedelta(minutes=1)
    assert po.priority == "normal"
    assert po.payment_terms == ""
    assert response.data["payment_schedule"]["due_date"] is None


def test_create_rejects_an_order_date_a_year_out():
    client = _client(_staff())
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "order_date": (timezone.now() + timedelta(days=400)).isoformat(),
            "items": [{"item_supplier_id": item_supplier.id, "quantity": 1, "unit_cost": "1.00"}],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "order_date" in response.data["error"]["details"]


def test_create_rejects_an_unknown_payment_term():
    client = _client(_staff())
    supplier = SupplierFactory()
    item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

    response = client.post(
        "/api/reorders/purchase-orders/",
        {
            "supplier": str(supplier.id),
            "payment_terms": "net_45",
            "items": [{"item_supplier_id": item_supplier.id, "quantity": 1, "unit_cost": "1.00"}],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "payment_terms" in response.data["error"]["details"]


# ─────────────────────────────────────────────────────────────────────────────
# Update API
# ─────────────────────────────────────────────────────────────────────────────
def test_patch_corrects_the_order_date_after_the_order_went_out():
    user = _staff()
    client = _client(user)
    po = _po(user, status=PurchaseOrder.Status.SENT)
    _line(po)
    actually_placed = timezone.now() - timedelta(days=21)

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/",
        {"order_date": actually_placed.isoformat()},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    po.refresh_from_db()
    assert po.order_date == actually_placed


def test_patch_sets_the_three_terms_and_the_schedule_follows():
    user = _staff()
    client = _client(user)
    placed = timezone.now() - timedelta(days=3)
    po = _po(user, status=PurchaseOrder.Status.SENT, order_date=placed)
    _line(po, quantity_ordered=4, unit_cost_ordered=Decimal("2.50"))
    po.calculate_estimated_total()
    po.save()

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/",
        {"priority": "high", "payment_terms": "net_60", "freight_terms": "collect"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    po.refresh_from_db()
    assert (po.priority, po.payment_terms, po.freight_terms) == (
        "high",
        "net_60",
        "collect",
    )
    assert response.data["payment_schedule"] == {
        "due_date": (placed.date() + timedelta(days=60)).isoformat(),
        "amount": "10.00",
        "basis": "Net 60 from order date",
    }


def test_patch_clears_terms_back_to_unagreed():
    user = _staff()
    client = _client(user)
    po = _po(
        user,
        status=PurchaseOrder.Status.SENT,
        payment_terms=PurchaseOrder.PaymentTerms.NET_30,
        freight_terms=PurchaseOrder.FreightTerms.COLLECT,
    )
    _line(po)

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/",
        {"payment_terms": "", "freight_terms": ""},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    po.refresh_from_db()
    assert po.payment_terms == ""
    assert po.freight_terms == ""
    assert response.data["payment_schedule"]["due_date"] is None


def test_patch_rejects_an_order_date_a_year_out():
    user = _staff()
    client = _client(user)
    po = _po(user, status=PurchaseOrder.Status.SENT)
    _line(po)
    original = po.order_date

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/",
        {"order_date": (timezone.now() + timedelta(days=400)).isoformat()},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    po.refresh_from_db()
    assert po.order_date == original


def test_terms_are_metadata_only_and_move_no_stock():
    """Setting terms touches no stock level and logs no usage."""
    from inventory.models import UsageLog

    user = _staff()
    client = _client(user)
    po = _po(user, status=PurchaseOrder.Status.SENT)
    line = _line(po)
    item = line.item_supplier.item
    before = item.current_stock

    response = client.patch(
        f"/api/reorders/purchase-orders/{po.id}/",
        {"priority": "urgent", "payment_terms": "cod", "freight_terms": "prepaid"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    item.refresh_from_db()
    assert item.current_stock == before
    assert not UsageLog.objects.filter(item=item).exists()


# ─────────────────────────────────────────────────────────────────────────────
# Admin
# ─────────────────────────────────────────────────────────────────────────────
def test_admin_surfaces_the_new_header_fields_and_lets_order_date_be_edited():
    admin_fields = {
        name for _, options in PurchaseOrderAdmin.fieldsets for name in options["fields"]
    }

    assert {"priority", "payment_terms", "freight_terms"} <= admin_fields
    assert "order_date" in admin_fields
    assert "order_date" not in PurchaseOrderAdmin.readonly_fields
    assert PurchaseOrderAdmin.date_hierarchy == "order_date"
    assert {"priority", "payment_terms", "freight_terms"} <= set(PurchaseOrderAdmin.list_filter)


def test_admin_change_page_renders_the_derived_schedule():
    admin_user = UserFactory(is_staff=True, is_superuser=True)
    po = _po(admin_user, payment_terms=PurchaseOrder.PaymentTerms.NET_30)
    _line(po, quantity_ordered=2, unit_cost_ordered=Decimal("5.00"))
    po.calculate_estimated_total()
    po.save()

    client = Client()
    client.force_login(admin_user)
    response = client.get(f"/admin/reorder_queue/purchaseorder/{po.pk}/change/")

    assert response.status_code == status.HTTP_200_OK
    assert "Net 30 from order date" in response.content.decode()


def test_admin_add_page_renders_without_a_saved_order():
    """The schedule readonly field must survive an unsaved instance."""
    admin_user = UserFactory(is_staff=True, is_superuser=True)
    client = Client()
    client.force_login(admin_user)

    response = client.get("/admin/reorder_queue/purchaseorder/add/")

    assert response.status_code == status.HTTP_200_OK
