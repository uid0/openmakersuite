"""Work-order lockout/tagout (LOTO) completion rows (WO-OMR PR4).

At WO generation one :class:`~inventory.models.WorkOrderLotoCompletion` row is
materialized per ``loto.AssetEnergySource`` on the WO's asset — mirroring the
task/material completion rows — so a scanned-back paper form has rows to apply
its ``loto_<id>`` checkbox marks against.

The energy source's descriptive fields are denormalized onto the row here so the
printed sheet and the persisted record survive a later edit/delete of the source
(the ``energy_source`` FK is ``SET_NULL``), exactly like ``task_title`` on
``WorkOrderTaskCompletion``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventory.models import WorkOrder, WorkOrderLotoCompletion
    from loto.models import AssetEnergySource


def energy_source_label(source: "AssetEnergySource") -> str:
    """Human label for an energy source, e.g. ``"Electrical (240V)"``."""
    label = source.get_source_type_display()
    if source.magnitude:
        label = f"{label} ({source.magnitude})"
    return label


def _required_devices_summary(source: "AssetEnergySource") -> str:
    """Comma-joined device labels required to isolate this source (denormalized)."""
    return ", ".join(d.label for d in source.required_devices.all())


def build_loto_completion(work_order: "WorkOrder", source: "AssetEnergySource"):
    """Construct (unsaved) a LOTO completion row denormalized from ``source``."""
    from inventory.models import WorkOrderLotoCompletion

    return WorkOrderLotoCompletion(
        work_order=work_order,
        energy_source=source,
        source_type=source.source_type,
        source_label=energy_source_label(source),
        isolation_point=source.isolation_point or "",
        required_devices=_required_devices_summary(source),
    )


def create_loto_completions(work_order: "WorkOrder") -> "list[WorkOrderLotoCompletion]":
    """Materialize LOTO completion rows for every energy source on the WO's asset.

    One row per :class:`loto.AssetEnergySource`, with the descriptive fields
    denormalized. Skips energy sources that already have a completion row on this
    WO, so a reprint / bundle path can call it again without creating duplicates.
    An asset with no energy sources yields no rows (and, downstream, no LOTO
    checkboxes on the PDF) — never an error. Returns the rows created.
    """
    from inventory.models import WorkOrderLotoCompletion

    # Straight off the work order: a corrective WO has no PM template but is
    # every bit as capable of needing the machine locked out.
    asset = work_order.asset
    if asset is None:
        return []

    already = set(
        work_order.loto_completions.exclude(energy_source__isnull=True).values_list(
            "energy_source_id", flat=True
        )
    )
    sources = asset.energy_sources.exclude(id__in=already).prefetch_related("required_devices")
    rows = [build_loto_completion(work_order, source) for source in sources]
    if rows:
        WorkOrderLotoCompletion.objects.bulk_create(rows)
    return rows
