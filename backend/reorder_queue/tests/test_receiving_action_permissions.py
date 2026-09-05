"""Every non-list action on the receiving surface refuses an anonymous caller.

Two documents promise this. ``docs/PO_RECEIVING_API.md`` opens with "Every
endpoint below requires an authenticated user", and
``docs/API_PERMISSION_MATRIX.md`` records the purchase-order ``@action``s as
login-required because they expose supplier part numbers, barcodes and costs
while the plain list and detail reads stay open for the queue dashboard.

Nothing was checking that. ``api_permission_matrix.yaml`` pins the *declared*
``permission_classes``, which on these viewsets is not where the gate lives —
``PurchaseOrderViewSet.get_permissions`` decides it at request time — so a
declaration-level snapshot cannot tell whether an endpoint is actually
reachable anonymously.

The route list is DERIVED from the project's real URLconf, not written out
here. An ``@action`` added later is covered the moment it is routed, without
anyone remembering to add a case; if it is deliberately public, this test is
where that decision has to be stated.
"""

from __future__ import annotations

from django.urls import get_resolver, reverse
from django.urls.resolvers import URLPattern, URLResolver
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.tests.factories import SupplierFactory
from reorder_queue.models import PurchaseOrder
from reorder_queue.views import OrderReceiptViewSet, PurchaseOrderViewSet

#: The viewsets whose non-list endpoints this surface's documents describe.
GATED_VIEWSETS = (PurchaseOrderViewSet, OrderReceiptViewSet)

#: Router-generated names for the plain CRUD routes. Anonymous READS of the
#: purchase-order list and detail are deliberate — the queue dashboard renders
#: without a login — so those are excluded rather than asserted refused.
CRUD_ACTIONS = frozenset({"list", "retrieve", "create", "update", "partial_update", "destroy"})


def routed_actions():
    """``(name, method, url_kwarg_names)`` for every non-CRUD route on the surface.

    Walks the real URLconf, so what is enumerated is what is actually reachable
    over HTTP rather than what a decorator was believed to declare.

    The router emits a ``.json``-suffixed twin of every route; those are dropped
    because the bare route is the same endpoint behind the same permission. That
    is verified rather than assumed — a route that existed ONLY in suffixed form
    would be dropped coverage, so it raises instead.
    """
    plain, suffixed = [], set()

    def walk(patterns):
        for entry in patterns:
            if isinstance(entry, URLResolver):
                walk(entry.url_patterns)
                continue
            if not isinstance(entry, URLPattern):
                continue
            callback = entry.callback
            if getattr(callback, "cls", None) not in GATED_VIEWSETS:
                continue
            actions = getattr(callback, "actions", None) or {}
            if set(actions.values()) & CRUD_ACTIONS:
                continue
            kwarg_names = tuple(entry.pattern.regex.groupindex)
            if "format" in kwarg_names:
                suffixed.add(entry.name)
                continue
            for method in actions:
                plain.append((entry.name, method, kwarg_names))

    walk(get_resolver().url_patterns)

    orphaned = suffixed - {name for name, _, _ in plain}
    if orphaned:
        raise AssertionError(
            "format-suffixed routes with no plain twin would go unchecked: "
            + ", ".join(sorted(orphaned))
        )
    return plain


@pytest.mark.django_db
class TestAnonymousCallersAreRefused:
    def test_the_surface_actually_has_actions_to_check(self):
        """A derived list that silently came back empty would prove nothing."""
        assert len(routed_actions()) >= 10

    def test_every_non_list_action_refuses_an_anonymous_caller(self, django_user_model):
        owner = django_user_model.objects.create_user(username="po-owner", password="pw")
        purchase_order = PurchaseOrder.objects.create(
            supplier=SupplierFactory(name="Grainger"),
            status=PurchaseOrder.Status.SENT,
            order_date=timezone.now(),
            created_by=owner,
        )
        # Any routed id resolves the URL; the permission check runs first, so
        # the object never has to exist for the refusal to be the real one.
        url_values = {"pk": purchase_order.pk, "item_id": 1, "attachment_id": 1}

        anonymous = APIClient()
        allowed_anonymously = []
        for name, method, kwarg_names in routed_actions():
            missing = [key for key in kwarg_names if key not in url_values]
            assert not missing, (
                f"{name} routes on {missing}, which this check does not know how to "
                "fill — add it to url_values rather than letting the route go unchecked"
            )
            url = reverse(name, kwargs={key: url_values[key] for key in kwarg_names})
            response = getattr(anonymous, method)(url, {}, format="json")
            if response.status_code not in (401, 403):
                allowed_anonymously.append(f"{method.upper()} {url} -> {response.status_code}")

        assert not allowed_anonymously, (
            "These endpoints answered an unauthenticated caller. Either gate them or, "
            "if the exposure is deliberate, say so here and in "
            "docs/API_PERMISSION_MATRIX.md:\n" + "\n".join(allowed_anonymously)
        )

    def test_the_purchase_order_list_and_detail_are_closed_too(self, django_user_model):
        """This test was named ``..._stay_open`` and asserted 200 on both.

        It described the one deliberate hole in this class: ``list`` and
        ``retrieve`` were ``AllowAny`` "for the queue dashboard". The captain
        closed it (op-anonymous-read-posture) — a purchase order is the
        supplier's name, the agreement, their order number, the payment terms
        and every line's cost, and the "cost data is filtered in the serializer"
        claim in the permission matrix that justified leaving it open was false.
        Inverted rather than deleted, because the class's whole subject is which
        of these actions an anonymous caller reaches, and this is now none.
        """
        owner = django_user_model.objects.create_user(username="po-owner-2", password="pw")
        purchase_order = PurchaseOrder.objects.create(
            supplier=SupplierFactory(name="Fastenal"),
            status=PurchaseOrder.Status.SENT,
            order_date=timezone.now(),
            created_by=owner,
        )

        anonymous = APIClient()
        listing = anonymous.get(reverse("purchaseorder-list"))
        detail = anonymous.get(reverse("purchaseorder-detail", args=[purchase_order.pk]))

        assert listing.status_code in (401, 403)
        assert detail.status_code in (401, 403)
        assert b"Fastenal" not in listing.content
        assert b"Fastenal" not in detail.content

    def test_a_signed_in_caller_still_reads_the_list_and_detail(self, django_user_model):
        """CONTROL: closed to anonymous callers, not to the operators who use it."""
        owner = django_user_model.objects.create_user(username="po-owner-3", password="pw")
        purchase_order = PurchaseOrder.objects.create(
            supplier=SupplierFactory(name="Fastenal"),
            status=PurchaseOrder.Status.SENT,
            order_date=timezone.now(),
            created_by=owner,
        )

        client = APIClient()
        client.force_authenticate(user=owner)

        assert client.get(reverse("purchaseorder-list")).status_code == 200
        detail = client.get(reverse("purchaseorder-detail", args=[purchase_order.pk]))
        assert detail.status_code == 200
        assert b"Fastenal" in detail.content
