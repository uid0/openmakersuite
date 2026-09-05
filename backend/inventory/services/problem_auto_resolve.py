"""Auto-resolve the problem reports a finished work order was promoted from.

A reported problem (``AssetProblem`` / ``LocationProblem``) that has been
promoted to a work order is *tracked by* that work order — nobody goes back to
the report to close it by hand. So when the work order finishes, the reports it
came from resolve with it.

"Finishes" has no single signal in this codebase, so every path that closes a
work order calls :func:`resolve_problems_for_work_order`:

* in-house, on screen — ``WorkOrderViewSet.perform_update``;
* in-house, off a paper form — ``work_order_ingest._apply_pm_submission``
  (emailed in) and ``omr_confirm_completion`` (reviewed scan);
* vendor — ``maintenance_orders.transitions.close_work_order``, which is the
  only completion signal that workflow has.

The function duck-types on the two reverse accessors both work-order models
carry (``asset_problems`` / ``location_problems``) rather than branching on
type, so the stamp is written identically whoever did the work.
"""

from __future__ import annotations

from django.utils import timezone

from membership.actor import SYSTEM_ACTOR


def resolve_problems_for_work_order(work_order, *, actor=None, notes: str = "") -> int:
    """Resolve every open problem promoted to ``work_order``. Returns the count.

    ``work_order`` is either an :class:`~inventory.models.WorkOrder` or a
    :class:`~maintenance_orders.models.ThirdPartyWorkOrder` — both expose
    ``asset_problems`` and ``location_problems``.

    Already-resolved (or closed) reports are left alone, so a re-save of a
    completed work order can't overwrite the original resolution stamp with a
    later timestamp. ``notes`` (the work order's completion notes) is copied
    into ``resolution_notes`` only when the report has none of its own.

    The stamp itself is
    :func:`inventory.services.problem_settlement.settle_problem`, shared with
    both API resolve routes, the ``LocationProblem`` one and the admin actions.
    Every row this touches is entering settlement from ``reported``/
    ``in_progress``, so the shared rule stamps each one — which is exactly what
    this function did with its own copy, and one copy fewer is the point. The
    loop stays because of the three things the rule has no opinion on: one
    moment shared across the batch, the ``SYSTEM_ACTOR`` fallback for an
    actor-less background completion, and the ``update_fields`` save.
    """
    from inventory.models import AssetProblem
    from inventory.services.problem_settlement import settle_problem

    now = timezone.now()
    open_statuses = (AssetProblem.Status.REPORTED, AssetProblem.Status.IN_PROGRESS)
    count = 0

    for manager in (work_order.asset_problems, work_order.location_problems):
        for problem in manager.filter(status__in=open_statuses):
            # ``resolved_by`` is a free-text column; an actor-less call is a
            # background completion, which the actor convention labels "System".
            settle_problem(
                problem,
                new_status=problem.__class__.Status.RESOLVED,
                actor=actor,
                actor_name=SYSTEM_ACTOR,
                at=now,
                save=False,
            )
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
