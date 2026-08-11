"""Work-order tool rows — the per-job tool list (op-0v4).

At PM work-order generation, one :class:`~inventory.models.WorkOrderTool` is
copied from each :class:`~inventory.models.MaintenanceTool` on the template,
mirroring how :class:`~inventory.models.WorkOrderMaterialUsage` rows are copied
from the template's materials. The copy is what the job displays and prints, so
a later edit to the template does not rewrite a job already in flight, and a
tech can restage a tool for *this* job by editing the row's ``location_hint``
without touching every future work order.

A corrective work order has no template to copy from and so starts with no
rows — it gains tools only ad-hoc, through ``WorkOrderViewSet.add_tool``.

Tools are gathered, used and returned, so nothing here touches stock: no
decrement, no usage log, no cost, no purchase-order bridge. Compare
``work_order_material_usage``, which has all four.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventory.models import WorkOrder, WorkOrderTool


def build_work_order_tool(work_order: "WorkOrder", tool) -> "WorkOrderTool":
    """Construct (unsaved) a work-order tool row frozen from a template tool.

    Every display field is copied, because the row is read directly and the
    ``tool`` FK is provenance only — the same rule ``material_name`` follows on
    a material usage row.
    """
    from inventory.models import WorkOrderTool

    return WorkOrderTool(
        work_order=work_order,
        tool=tool,
        inventory_item=tool.inventory_item,
        is_ad_hoc=False,
        name=tool.name,
        quantity=tool.quantity,
        location_hint=tool.location_hint,
        is_required=tool.is_required,
        notes=tool.notes,
    )


def create_work_order_tools(work_order: "WorkOrder") -> "list[WorkOrderTool]":
    """Copy the PM template's tools onto ``work_order``. Returns the rows made.

    Skips template tools this work order already carries a row for, so a
    bundle / regeneration path can call it again without duplicating. A
    corrective work order (no ``maintenance_item``) yields no rows — never an
    error; it is simply the case that has no template to copy.
    """
    from inventory.models import WorkOrderTool

    item = work_order.maintenance_item
    if item is None:
        return []

    already_copied = set(
        work_order.tools.filter(tool__isnull=False).values_list("tool_id", flat=True)
    )
    rows = [
        build_work_order_tool(work_order, tool)
        for tool in item.tools.all()
        if tool.id not in already_copied
    ]
    return WorkOrderTool.objects.bulk_create(rows)
