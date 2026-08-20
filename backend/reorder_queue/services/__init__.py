"""Service layer for reorder-queue purchase-order + receiving workflow (#883).

Views keep the serializers as the request/response boundary and keep their
``record_audit_event`` calls; the workflow bodies live in these services.
"""

from .approvals import (
    APPROVED_REQUESTS_ATTR,
    PO_ELIGIBLE_STATUSES,
    approved_requests_prefetch,
    get_approved_reorder_request,
)
from .numbering import next_po_number
from .purchase_orders import (
    DRAFT_ONLY_EDIT_MESSAGE,
    add_business_days,
    add_line_items,
    apply_line_quantity,
    confirm_order,
    create_line_item,
    create_purchase_order,
    mark_sent,
    recalculate_estimated_total,
    update_reorder_requests_from_po,
    void_line_item,
    void_po,
)
from .receiving import (
    close_linked_reorder_request,
    create_lead_time_log,
    mark_delivered_receipt,
    receive_delivery,
)

__all__ = [
    "APPROVED_REQUESTS_ATTR",
    "PO_ELIGIBLE_STATUSES",
    "approved_requests_prefetch",
    "get_approved_reorder_request",
    "DRAFT_ONLY_EDIT_MESSAGE",
    "add_business_days",
    "add_line_items",
    "apply_line_quantity",
    "confirm_order",
    "create_line_item",
    "create_purchase_order",
    "mark_sent",
    "next_po_number",
    "recalculate_estimated_total",
    "update_reorder_requests_from_po",
    "void_line_item",
    "void_po",
    "close_linked_reorder_request",
    "create_lead_time_log",
    "mark_delivered_receipt",
    "receive_delivery",
]
