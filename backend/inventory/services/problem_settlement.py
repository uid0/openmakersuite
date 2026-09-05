"""THE rule for stamping a problem report as it reaches a settled state.

``AssetProblem`` is moved to ``resolved``/``closed`` by four writers — the two
API actions (``AssetProblemViewSet.resolve``, which is the route ScanTTY uses,
and ``AssetViewSet.resolve_problem``), ``AssetProblemAdmin``'s two changelist
actions, and :mod:`inventory.services.problem_auto_resolve` for a finished work
order. Each carried its own copy of the stamp, and the copies disagreed. This
module holds the one rule they all now apply, because a rule written out four
times is a rule that will be joined incompletely by the fifth writer.

The rule, in one sentence: **a new resolution restamps, a close preserves.**

  * Settling INTO ``resolved`` asserts that the work has just been done, so it
    always writes ``resolved_at``/``resolved_by`` for whoever did it now. A
    report whose ``status`` was edited back to ``reported`` after a previous
    occurrence still carries that occurrence's stamp — nothing clears it — so
    inheriting it would show today's fix with a months-old date and somebody
    else's name, on ``AssetProblemSerializer`` and in ScanTTY, with no error.
  * Settling into ``closed`` is a FILING change, not a resolution. It fills the
    stamp only when the report has none, so filing away a report somebody else
    resolved never takes their credit.

:func:`resolve_problems_for_work_order` is consistent with this rule rather than
a caller of it: it only ever settles into ``resolved``, and it stamps
unconditionally, which is exactly what the rule says a new resolution does. It
keeps its own loop because it also carries a shared moment across the batch, a
``SYSTEM_ACTOR`` fallback for an actor-less background completion, an
``update_fields`` save, and its own open-status filter.
"""

from __future__ import annotations

from django.utils import timezone

from membership.actor import actor_display


def settle_problem(problem, *, new_status, actor, save=True):
    """Move ``problem`` to ``new_status`` and stamp it by the rule above.

    ``new_status`` is ``AssetProblem.Status.RESOLVED`` or ``CLOSED``; the caller
    owns validating it and owns any other field it wants written (an API route
    sets ``resolution_notes`` before calling). ``actor`` is the performing user,
    collapsed to the free-text ``resolved_by`` column through
    :func:`membership.actor.actor_display`.

    Pass ``save=False`` to have the fields set on the instance without a write,
    for a caller batching its own ``save()``. Returns ``problem``.
    """
    problem.status = new_status
    if _is_new_resolution(problem, new_status) or not problem.resolved_at:
        problem.resolved_at = timezone.now()
        problem.resolved_by = actor_display(actor)
    if save:
        problem.save()
    return problem


def _is_new_resolution(problem, new_status):
    """Whether settling into ``new_status`` is a resolution rather than a filing.

    Derived from the target state, not from a flag each caller passes: every
    writer that lands a report in ``resolved`` is claiming the work was done,
    and every writer that lands it in ``closed`` is filing. A caller cannot get
    this wrong by describing itself incorrectly.
    """
    return new_status == problem.__class__.Status.RESOLVED
