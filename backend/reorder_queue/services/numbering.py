"""Purchase-order number composition (gh #887).

``PurchaseOrder.auto_generate_po_number`` delegates the ``PO-YYYY-NNNN``
composition here, but the method itself — and the ``save()`` retry loop that
recovers from uniqueness collisions — stay on the model: the concurrency seam
and its ``patch.object(PurchaseOrder, "auto_generate_po_number", ...)`` test
rely on that method remaining patchable, and ~20 bare
``PurchaseOrder.objects.create()`` callers rely on ``save()`` numbering them.
"""

from __future__ import annotations


def next_po_number(year: int) -> str:
    """Return the next ``PO-YYYY-NNNN`` number for ``year``.

    Reads the highest existing number for the year and increments its trailing
    counter, falling back to ``0001`` when none exist or the counter can't be
    parsed. Not collision-proof under concurrency on its own —
    ``PurchaseOrder.save()`` wraps the insert in a retry loop for that.
    """
    from reorder_queue.models import PurchaseOrder

    last_po = (
        PurchaseOrder.objects.filter(po_number__startswith=f"PO-{year}-")
        .order_by("-po_number")
        .first()
    )
    if last_po:
        try:
            next_num = int(last_po.po_number.split("-")[-1]) + 1
        except (ValueError, IndexError):
            next_num = 1
    else:
        next_num = 1

    return f"PO-{year}-{next_num:04d}"
