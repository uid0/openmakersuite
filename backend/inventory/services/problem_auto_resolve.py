"""Auto-resolve the problem reports a finished work order was promoted from.

A reported problem (``AssetProblem`` / ``LocationProblem``) that has been
promoted to a work order is *tracked by* that work order — nobody goes back to
the report to close it by hand. So when the work order finishes, the reports it
came from resolve with it.

"Finishes" means two different things depending on where the work happened:

* in-house — ``WorkOrder.status`` transitions to ``completed``
  (``WorkOrderViewSet.perform_update``);
* vendor — ``ThirdPartyWorkOrder`` reaches ``closed``
  (``maintenance_orders.transitions.close_work_order``).

Both call :func:`resolve_problems_for_work_order`. It duck-types on the two
reverse accessors both models carry (``asset_problems`` / ``location_problems``)
rather than branching on type, so the stamp is written identically either way.
"""

from __future__ import annotations

from django.utils import timezone

from membership.actor import SYSTEM_ACTOR, actor_display


def resolve_problems_for_work_order(work_order, *, actor=None, notes: str = "") -> int:
    """Resolve every open problem promoted to ``work_order``. Returns the count.

    ``work_order`` is either an :class:`~inventory.models.WorkOrder` or a
    :class:`~maintenance_orders.models.ThirdPartyWorkOrder` — both expose
    ``asset_problems`` and ``location_problems``.

    Already-resolved (or closed) reports are left alone, so a re-save of a
    completed work order can't overwrite the original resolution stamp with a
    later timestamp. ``notes`` (the work order's completion notes) is copied
    into ``resolution_notes`` only when the report has none of its own.
    """
    from inventory.models import AssetProblem

    # ``resolved_by`` is a free-text column; an actor-less call is a background
    # completion, which the actor convention labels "System".
    resolved_by = actor_display(actor, name=SYSTEM_ACTOR)
    now = timezone.now()
    open_statuses = (AssetProblem.Status.REPORTED, AssetProblem.Status.IN_PROGRESS)
    count = 0

    for manager in (work_order.asset_problems, work_order.location_problems):
        for problem in manager.filter(status__in=open_statuses):
            problem.status = problem.__class__.Status.RESOLVED
            problem.resolved_at = now
            problem.resolved_by = resolved_by
            # Promotion seeds the work order's notes with the report text, so
            # skip the copy when they still match — echoing the complaint back
            # as its own resolution says nothing.
            if notes and notes != problem.description and not problem.resolution_notes:
                problem.resolution_notes = notes
            problem.save(
                update_fields=[
                    "status",
                    "resolved_at",
                    "resolved_by",
                    "resolution_notes",
                    "updated_at",
                ]
            )
            count += 1
    return count
