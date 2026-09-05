"""THE rule for stamping a problem report as it reaches a settled state.

A problem report is moved to ``resolved``/``closed`` by five writers — the two
``AssetProblem`` API actions (``AssetProblemViewSet.resolve``, which is the
route ScanTTY uses, and ``AssetViewSet.resolve_problem``),
``LocationProblemViewSet.resolve`` on the sibling model,
``AssetProblemAdmin``'s two changelist actions, and
:mod:`inventory.services.problem_auto_resolve` for a finished work order. Each
carried its own copy of the stamp and the copies disagreed. They all call this
one rule now, because a rule written out five times is a rule the sixth writer
will join incompletely.

What the columns MEAN, which is what the rule is derived from
-------------------------------------------------------------
``resolved_at``/``resolved_by`` record **when the report entered a settled
state, and who put it there.** That fact is about the SOURCE state, not the
target — two earlier drafts of this rule reasoned about the target and each got
one case wrong for it:

  * keying on "settling into ``closed``" preserved a stale stamp on a report
    whose ``status`` had been edited back to ``reported`` after a recurrence, so
    today's fix showed a months-old date and somebody else's name;
  * keying on "settling into ``resolved``" then overwrote the FIRST resolver
    when an already-resolved report was resolved a second time — a stale detail
    page, or any client re-POSTing the action — and neither API route carries a
    status precondition.

Read as one predicate on the transition, both cases fall out:

    entering_settlement = status in {reported, in_progress}
                          and new_status in {resolved, closed}

An entry into settlement always stamps. A move that is already inside
settlement — ``resolved`` -> ``resolved``, ``resolved`` -> ``closed`` — is a
filing change, and never overwrites the moment the row actually settled.

The ``or not problem.resolved_at`` arm below is the deliberate exception: a
settled row carrying a NULL ``resolved_at`` is a legacy row damaged by the
pre-fix admin action, and filling that gap is right. Filling a gap is not the
same as overwriting a stamp, and only the first is allowed.

Both models qualify without a branch: ``AssetProblem.Status`` and
``LocationProblem.Status`` declare the same four members, so the sets are read
off ``problem.__class__.Status`` the way ``problem_auto_resolve`` already
duck-types the two.
"""

from __future__ import annotations

from django.utils import timezone

from membership.actor import actor_display


def settle_problem(problem, *, new_status, actor, actor_name="", at=None, save=True):
    """Move ``problem`` to ``new_status``, stamping it by the rule above.

    ``new_status`` is the ``RESOLVED`` or ``CLOSED`` member of the report's own
    ``Status``; the caller owns validating it, owns any status precondition it
    wants to keep, and owns any other field it writes (an API route sets
    ``resolution_notes`` before calling).

    ``actor`` is the performing user, collapsed to the free-text ``resolved_by``
    column through :func:`membership.actor.actor_display`; ``actor_name`` is the
    fallback that function uses when there is no authenticated user, which is
    how a background completion labels itself ``SYSTEM_ACTOR``. ``at`` pins the
    moment, for a batch that wants one moment across every row it settles.

    Pass ``save=False`` to have the fields set without a write, for a caller
    batching its own ``save()``. Returns ``problem``.
    """
    settled = _settled_statuses(problem)
    entering_settlement = problem.status not in settled and new_status in settled
    if entering_settlement or not problem.resolved_at:
        problem.resolved_at = at or timezone.now()
        problem.resolved_by = actor_display(actor, name=actor_name)
    problem.status = new_status
    if save:
        problem.save()
    return problem


def _settled_statuses(problem):
    """The report's settled states, read off its own ``Status``.

    ``reported``/``in_progress`` are the unsettled pair and
    ``resolved``/``closed`` the settled one, on both ``AssetProblem`` and
    ``LocationProblem``. Naming the settled half is enough: "not settled" is
    then every other state the model has, so a status added later defaults to
    counting as an entry into settlement rather than being silently treated as
    already-settled and losing its stamp.
    """
    status = problem.__class__.Status
    return (status.RESOLVED, status.CLOSED)
