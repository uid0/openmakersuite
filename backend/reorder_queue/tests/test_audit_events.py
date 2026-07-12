from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.tests.factories import ItemSupplierFactory, SupplierFactory
from reorder_queue.audit import record_event
from reorder_queue.models import (
    PurchaseOrder,
    PurchaseOrderAttachment,
    PurchaseOrderAuditEvent,
    PurchaseOrderItem,
)
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


class TestPurchaseOrderAuditEvents:
    def _client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _make_po(self, user, *, supplier=None, status=PurchaseOrder.Status.DRAFT):
        if supplier is None:
            supplier = SupplierFactory()
        return PurchaseOrder.objects.create(
            supplier=supplier,
            status=status,
            created_by=user,
        )

    def _make_line_item(self, purchase_order, *, quantity=2, quantity_received=0):
        return PurchaseOrderItem.objects.create(
            purchase_order=purchase_order,
            description="Test line item",
            quantity_ordered=quantity,
            quantity_received=quantity_received,
            unit_cost_ordered=Decimal("12.50"),
        )

    def _make_attachment(self, purchase_order, user, *, name="supplier-note.txt"):
        return PurchaseOrderAttachment.objects.create(
            purchase_order=purchase_order,
            file=SimpleUploadedFile(name, b"attachment content", content_type="text/plain"),
            uploaded_by=user,
        )

    def test_po_create_records_audit_event(self):
        user = UserFactory()
        client = self._client_for(user)
        supplier = SupplierFactory()
        item_supplier = ItemSupplierFactory(supplier=supplier, quantity_per_package=1)

        response = client.post(
            reverse("purchaseorder-list"),
            {
                "supplier": supplier.id,
                "items": [
                    {
                        "item_supplier_id": item_supplier.id,
                        "quantity": 3,
                        "unit_cost": "9.99",
                    }
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data

        event = PurchaseOrderAuditEvent.objects.get()
        assert event.action == PurchaseOrderAuditEvent.Action.PO_CREATE
        assert event.actor == user
        assert event.purchase_order_id == response.data["id"]
        assert event.metadata["po_number"]

    def test_po_void_records_audit_event(self):
        user = UserFactory(is_staff=True)
        client = self._client_for(user)
        po = self._make_po(user)

        response = client.post(
            reverse("purchaseorder-void", kwargs={"pk": po.pk}),
            {"reason": "wrong supplier"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        po.refresh_from_db()
        assert po.status == PurchaseOrder.Status.VOIDED

        event = PurchaseOrderAuditEvent.objects.get()
        assert event.action == PurchaseOrderAuditEvent.Action.PO_VOID
        assert event.actor == user
        assert event.purchase_order == po
        assert event.notes == "wrong supplier"

    def test_po_void_already_voided_no_audit(self):
        user = UserFactory(is_staff=True)
        client = self._client_for(user)
        po = self._make_po(user, status=PurchaseOrder.Status.VOIDED)

        response = client.post(
            reverse("purchaseorder-void", kwargs={"pk": po.pk}),
            {"reason": "wrong supplier"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert PurchaseOrderAuditEvent.objects.count() == 0

    def test_po_void_unauthorized_no_audit(self):
        owner = UserFactory()
        user = UserFactory()
        client = self._client_for(user)
        po = self._make_po(owner)

        response = client.post(
            reverse("purchaseorder-void", kwargs={"pk": po.pk}),
            {"reason": "wrong supplier"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert PurchaseOrderAuditEvent.objects.count() == 0

    def test_po_line_void_records_audit_event(self):
        user = UserFactory()
        client = self._client_for(user)
        po = self._make_po(user)
        item = self._make_line_item(po, quantity_received=0)

        response = client.post(
            reverse("purchaseorder-void-item", kwargs={"pk": po.pk, "item_id": item.pk}),
            {"reason": "discontinued"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        event = PurchaseOrderAuditEvent.objects.get()
        assert event.action == PurchaseOrderAuditEvent.Action.PO_LINE_VOID
        assert event.actor == user
        assert event.line_item == item
        assert event.purchase_order == po
        assert event.notes == "discontinued"

    def test_po_mark_delivered_records_audit_event(self):
        user = UserFactory()
        client = self._client_for(user)
        po = self._make_po(user, status=PurchaseOrder.Status.SENT)
        self._make_line_item(po, quantity=4, quantity_received=0)
        delivery_date = timezone.now().date().isoformat()

        response = client.post(
            reverse("purchaseorder-mark-delivered", kwargs={"pk": po.pk}),
            {
                "delivery_date": delivery_date,
                "tracking_number": "1Z999",
                "carrier": "UPS",
                "receipt_notes": "left at dock",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data

        po.refresh_from_db()
        event = PurchaseOrderAuditEvent.objects.get()
        assert event.action == PurchaseOrderAuditEvent.Action.PO_MARK_DELIVERED
        assert event.actor == user
        assert event.purchase_order == po
        assert event.notes == "left at dock"
        assert event.metadata["tracking_number"] == "1Z999"
        assert event.metadata["carrier"] == "UPS"
        assert event.metadata["fully_received"] is True

    def test_po_upload_attachment_records_audit_event(self):
        user = UserFactory()
        client = self._client_for(user)
        po = self._make_po(user)

        response = client.post(
            reverse("purchaseorder-upload-attachment", kwargs={"pk": po.pk}),
            {
                "file": SimpleUploadedFile(
                    "invoice.txt",
                    b"invoice body",
                    content_type="text/plain",
                ),
                "description": "Invoice copy",
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data

        event = PurchaseOrderAuditEvent.objects.get()
        assert event.action == PurchaseOrderAuditEvent.Action.ATTACHMENT_ADD
        assert event.actor == user
        assert event.purchase_order == po
        assert event.attachment is not None
        assert event.metadata["filename"].endswith("invoice.txt")

    def test_po_destroy_attachment_records_audit_event(self):
        owner = UserFactory()
        user = UserFactory(is_staff=True)
        client = self._client_for(user)
        po = self._make_po(owner)
        attachment = self._make_attachment(po, owner, name="receipt.pdf")

        response = client.delete(
            reverse(
                "purchaseorder-destroy-attachment",
                kwargs={"pk": po.pk, "attachment_id": attachment.pk},
            )
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

        event = PurchaseOrderAuditEvent.objects.get()
        assert event.action == PurchaseOrderAuditEvent.Action.ATTACHMENT_REMOVE
        assert event.actor == user
        assert event.purchase_order == po
        assert event.attachment is None
        assert event.metadata["filename"].endswith("receipt.pdf")

    def test_record_event_with_anonymous_actor_creates_row_with_null_actor(self):
        user = UserFactory()
        po = self._make_po(user)

        event = record_event(
            action=PurchaseOrderAuditEvent.Action.PO_CREATE,
            actor=None,
            purchase_order=po,
        )

        assert event.actor is None
        assert PurchaseOrderAuditEvent.objects.get().actor is None

    def test_record_event_derives_po_from_line_item(self):
        user = UserFactory()
        po = self._make_po(user)
        item = self._make_line_item(po)

        event = record_event(
            action=PurchaseOrderAuditEvent.Action.PO_LINE_VOID,
            actor=user,
            line_item=item,
        )

        assert event.purchase_order == item.purchase_order
        assert event.line_item == item

    def test_record_event_derives_po_from_attachment(self):
        user = UserFactory()
        po = self._make_po(user)
        attachment = self._make_attachment(po, user)

        event = record_event(
            action=PurchaseOrderAuditEvent.Action.ATTACHMENT_ADD,
            actor=user,
            attachment=attachment,
        )

        assert event.purchase_order == attachment.purchase_order
        assert event.attachment == attachment
