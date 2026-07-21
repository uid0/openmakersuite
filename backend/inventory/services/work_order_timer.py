"""Work-order stopwatch: start/pause a WO and its individual steps (op-m3so).

The purpose is measurement, not policing: a work order completed electronically
should record how long it *actually* took, per step and overall, so
``MaintenanceItem.estimated_time_minutes`` on the template can be tuned against
real numbers instead of guesses.

The state lives on :class:`~inventory.models.ElapsedTimerModel` (an accumulator
plus the start of the segment currently running). This module owns the rules
that span more than one row:

* only one *step* on a work order runs at a time — starting step B pauses
  step A, so the per-step totals partition the work instead of overlapping;
* marking a step complete stops that step's clock;
* closing the work order commits every running segment and lands the total on
  the ``MaintenanceLog`` as ``time_spent_minutes``.

Everything here is idempotent: starting a running clock or pausing a stopped one
changes nothing and reports no error. Clients retry freely, and a double-tap on
a phone can't corrupt the total.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from django.utils import timezone

if TYPE_CHECKING:
    from inventory.models import WorkOrder, WorkOrderTaskCompletion

#: Accepted ``action`` values on the timer endpoints.
START = "start"
PAUSE = "pause"
TIMER_ACTIONS = (START, PAUSE)


def start_work_order_timer(work_order: "WorkOrder", *, now=None) -> bool:
    """Start the work-order clock. Returns False if it was already running."""
    now = now or timezone.now()
    if not work_order.start_timer(now=now):
        return False
    work_order.save(update_fields=[*work_order.TIMER_FIELDS, "updated_at"])
    return True


def pause_work_order_timer(work_order: "WorkOrder", *, now=None) -> bool:
    """Commit the running work-order segment. False if already paused."""
    now = now or timezone.now()
    if not work_order.pause_timer(now=now):
        return False
    work_order.save(update_fields=[*work_order.TIMER_FIELDS, "updated_at"])
    return True


def pause_task_timer(task_completion: "WorkOrderTaskCompletion", *, now=None) -> bool:
    """Commit the running segment on one step. False if already paused."""
    now = now or timezone.now()
    if not task_completion.pause_timer(now=now):
        return False
    task_completion.save(update_fields=list(task_completion.TIMER_FIELDS))
    return True


def pause_other_task_timers(
    task_completion: "WorkOrderTaskCompletion", *, now=None
) -> list["WorkOrderTaskCompletion"]:
    """Pause every *other* running step on the same work order.

    Returns the rows that were actually stopped. The DB is the source of truth
    for "what else is running" — a caller holding a stale in-memory list of
    steps can't leave a second clock ticking.
    """
    from inventory.models import WorkOrderTaskCompletion

    now = now or timezone.now()
    stopped: list[WorkOrderTaskCompletion] = []
    others = WorkOrderTaskCompletion.objects.filter(
        work_order_id=task_completion.work_order_id, is_timing=True
    ).exclude(pk=task_completion.pk)
    for other in others:
        if pause_task_timer(other, now=now):
            stopped.append(other)
    return stopped


def start_task_timer(task_completion: "WorkOrderTaskCompletion", *, now=None) -> bool:
    """Start one step's clock, stopping whichever other step was running.

    Deliberately does *not* touch the work-order clock: WO elapsed is
    wall-time-on-job (which includes setup, LOTO, cleanup — time that belongs to
    no single step), so the two are started independently and the per-step
    totals are expected to sum to less than the WO total.
    """
    now = now or timezone.now()
    pause_other_task_timers(task_completion, now=now)
    if not task_completion.start_timer(now=now):
        return False
    task_completion.save(update_fields=list(task_completion.TIMER_FIELDS))
    return True


def apply_timer_action(obj, action: str, *, now=None) -> bool:
    """Dispatch ``"start"``/``"pause"`` to a work order or a step.

    Raises ``ValueError`` on an unknown action so the view can turn it into a
    400 rather than silently no-op'ing a typo.
    """
    from inventory.models import WorkOrder

    if action not in TIMER_ACTIONS:
        raise ValueError(f"action must be one of {', '.join(TIMER_ACTIONS)}")
    is_work_order = isinstance(obj, WorkOrder)
    if action == START:
        return (
            start_work_order_timer(obj, now=now)
            if is_work_order
            else start_task_timer(obj, now=now)
        )
    return pause_work_order_timer(obj, now=now) if is_work_order else pause_task_timer(obj, now=now)


def finalize_work_order_timers(work_order: "WorkOrder", *, now=None) -> int:
    """Stop every clock on a work order and return the committed WO total.

    Called when the work order becomes ``completed``, from every completion path
    (the digital status flip and the reviewed-scan confirm), so a WO can never
    be left with a clock running after it closes. Reopening a completed WO
    deliberately does *not* resume anything — the accumulated total stays put
    and the tech restarts the clock by hand if more work happens.
    """
    from inventory.models import WorkOrderTaskCompletion

    now = now or timezone.now()
    pause_work_order_timer(work_order, now=now)
    for step in WorkOrderTaskCompletion.objects.filter(work_order_id=work_order.pk, is_timing=True):
        pause_task_timer(step, now=now)
    return work_order.elapsed_seconds or 0


def recorded_minutes(work_order: "WorkOrder") -> Optional[int]:
    """Whole minutes on the WO clock, or None if it was never run.

    Rounds rather than truncates so a 90-second job logs 2 minutes, not 1; a
    clock that ran at all always reports at least 1 minute, because "0 minutes
    spent" reads as "no data" and would be indistinguishable from never having
    started the timer.
    """
    seconds = work_order.elapsed_seconds or 0
    if seconds <= 0:
        return None
    return max(1, round(seconds / 60))


def apply_elapsed_to_log(log, work_order: "WorkOrder") -> bool:
    """Stamp the WO's recorded total onto a ``MaintenanceLog``, if we may.

    Precedence: **a value already on the log always wins.** ``time_spent_minutes``
    is a hand-entered field on the manual "log maintenance" path, and a human who
    typed 45 knows something the stopwatch doesn't (it may have been left running
    over lunch). So this fills the field only when it is null, and only when the
    timer actually recorded something. Returns True if it wrote.
    """
    if log.time_spent_minutes is not None:
        return False
    minutes = recorded_minutes(work_order)
    if minutes is None:
        return False
    log.time_spent_minutes = minutes
    log.save(update_fields=["time_spent_minutes"])
    return True
