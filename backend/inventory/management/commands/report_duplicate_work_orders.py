"""Read-only report on *suspected* duplicate preventive-maintenance work orders.

Background (BACKEND-18)
-----------------------
``MaintenanceItemViewSet.generate_work_order`` committed a work order and *then*
raised a ``TypeError`` while serializing the response, so the operator saw an
HTTP 500 and retried. Every "failed" attempt left a real, fully populated work
order behind — its own task completions, material-usage rows, tool rows and
LOTO rows. Duplicates are indistinguishable from intentional work orders except
by identical ``maintenance_item`` + ``due_date`` and by timestamp clustering.

What this command does
----------------------
Groups work orders on ``(maintenance_item, due_date)`` — the investigation's
candidate query — and, for each group, prints who and what is attached to each
row so a human can decide which one to keep. It decides nothing and changes
nothing.

This command is deliberately, permanently READ-ONLY. There is no ``--fix``, no
``--merge``, no ``--delete`` and no dry-run that could be flipped to a real run.
Cleaning up duplicates is a separate, human-authorised decision; do not wire
cleanup into this file.

Usage::

    python manage.py report_duplicate_work_orders
    python manage.py report_duplicate_work_orders --window-seconds 120
    python manage.py report_duplicate_work_orders --format json > duplicates.json
    python manage.py report_duplicate_work_orders --format csv  > duplicates.csv
"""

from __future__ import annotations

import csv
import io
import json
import operator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from functools import reduce
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from inventory.models import MaintenanceAuditEvent, WorkOrder, WorkOrderTool

#: Rows created within this many seconds of each other are treated as one
#: burst. The BACKEND-18 retries landed seconds apart (an operator re-pressing
#: "w" after a 500), so a tight burst is the strongest available evidence that a
#: group is retry damage rather than deliberately repeated work.
DEFAULT_WINDOW_SECONDS = 300

#: ``updated_at`` is set by ``auto_now`` on the same ``save()`` that sets
#: ``created_at``, so the two differ by microseconds on an untouched row. Only
#: treat a row as "edited since creation" beyond this slack.
UPDATED_SLACK_SECONDS = 2

#: How many ``(maintenance_item, due_date)`` pairs to OR into one candidate
#: query. Fetching the exact pairs rather than the cross product of the two key
#: columns keeps the prefetches off rows that are not group members; chunking
#: keeps the SQL from growing unbounded on a production-sized run.
PAIR_CHUNK = 200

CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}

#: Three-state answer to "has this been worked?". "We checked and found
#: nothing" and "we cannot tell" are different answers, and only one of them
#: makes a work order safe to remove.
VERDICT_WORKED = "worked"
VERDICT_UNKNOWN = "unknown"
VERDICT_NONE = "no-recorded-work"

FALSE_POSITIVE_NOTE = """\
These are SUSPECTED duplicates, not proven ones. The grouping key is
(maintenance_item, due_date), which also matches work orders that were repeated
on purpose. Known causes of a false positive:

  * a PM that was legitimately re-scheduled or re-issued for the same due date
    (the first work order was voided/abandoned on paper and a fresh one raised);
  * a work order deliberately re-generated after the asset failed re-inspection,
    or split so two people could work the same PM on the same day;
  * a bulk generation run (``generate_work_orders_bulk``) plus a manual
    generation for the same item and date;
  * a paper form scanned back in as a new work order alongside the digital one;
  * any manual back-fill or data import that re-created historic work orders.

Timestamp clustering is what separates the two: the BACKEND-18 retries happened
seconds apart. Groups whose rows are spread over hours or days are ranked LOW
confidence and are most likely legitimate. Nothing is excluded from the report
on that basis — ranking, not filtering, so a real duplicate is never hidden."""

SIGNALS_NOTE = """\
"Worked" signals reported per work order (each is shown separately rather than
collapsed into one guess, because they fail differently):

  status              status beyond the initial "open".
  tasks_completed     WorkOrderTaskCompletion rows with is_completed=True.
  task_notes          task completions carrying free-text notes. Marking a step
                      NOT done still saves its notes, and that never moves the
                      work order's own status or updated_at.
  task_timers         task completions with per-step stopwatch time, or a step
                      whose stopwatch is running right now.
  materials_used      WorkOrderMaterialUsage rows with was_used=True. NOTE:
                      quantity_used alone is NOT evidence — generation pre-fills
                      it from the template's planned quantity, so a non-zero
                      quantity_used is present on a work order nobody touched.
  materials_applied   usage rows with applied_quantity set, i.e. stock was
                      actually decremented against this work order. This is the
                      hardest material signal: it has an inventory side effect.
  qty_edited          usage rows whose quantity_used differs from
                      quantity_planned, i.e. somebody changed the number.
  adhoc_materials     usage rows with is_ad_hoc=True — a material typed in
                      during the job. Generation never creates one, so its mere
                      existence is somebody working this work order.
  material_evidence   usage rows carrying a receipt image, a unit cost or a
                      purchase-order line: real money and real paperwork
                      attached to this row.
  adhoc_tools         WorkOrderTool rows with is_ad_hoc=True — a tool added
                      during the job rather than copied from the template.
  tools_restaged      tool rows whose location_hint or notes DIVERGE from the
                      template row they were copied from. A non-empty hint is
                      not evidence by itself: generation copies the template's
                      value in. Only a divergence means somebody restaged it.
  loto_completed      WorkOrderLotoCompletion rows with is_completed=True.
  loto_notes          LOTO rows carrying notes, plus the work order's own
                      free-text loto_completion_note.
  time_seconds        stopwatch time on the job (elapsed + any running segment).
  started_at          first time the timer was started.
  completed_at        set when the work order was marked complete.
  completed_by        completed_by_name from a scanned paper form.
  completed_scan      a completed paper form was scanned or emailed back in.
  bundled_items       sibling PMs were bundled onto this work order.
  maintenance_logs    MaintenanceLog rows written back from this work order.
  photos/attachments  evidence uploaded against the work order.
  validations         WorkOrderValidation sign-offs.
  submissions         scanned/emailed-in paper submissions.
  edited_since_create updated_at moved after created_at (someone saved the row).

A work order is called WORKED if any of those fire. If none fires but something
INDETERMINATE is present, the verdict is CANNOT TELL rather than "untouched" —
see the coverage note below for which is which. The retry artifacts left by
BACKEND-18 were never opened by anyone, so they should show none."""

COVERAGE_NOTE = """\
What "worked" does and does not cover — read this before removing anything.

CHECKED (any one of these makes the verdict WORKED): status beyond open; task
completions ticked, annotated, or carrying per-step stopwatch time; material
rows marked used, with stock applied, with an edited quantity, added ad hoc, or
carrying a receipt image / unit cost / purchase-order line; tool rows added ad
hoc or restaged away from the template's staging location or notes; LOTO rows
completed or annotated and the work order's free-text LOTO completion note;
work-order stopwatch time, started_at, completed_at, completed_by_name; a
completed paper form scanned back in; bundled additional maintenance items;
maintenance logs, photos, attachments, validations and paper submissions; and
updated_at having moved past created_at.

INDETERMINATE — reported as CANNOT TELL, never as absence of work: a work order
that is assigned to somebody; one that had a printed OMR form generated for it,
so a paper copy exists that may have been worked and never scanned back; one
carrying audit events other than wo_create; and tool rows whose template
counterpart has since been deleted, so a per-job restage cannot be told apart
from the value that was copied in at generation.

NOT COVERED AT ALL: these signals only see what OpenMakerSuite recorded. Work
done on paper and never scanned back, work logged against the asset rather than
against the work order, and anything recorded outside OMS entirely leave no
trace here. "No recorded work" therefore means "nothing found in the signals
listed above" — it does NOT mean "nobody worked it"."""


@dataclass
class WorkOrderRow:
    """One work order inside a suspected-duplicate group. Pure data."""

    id: str
    number: str
    status: str
    status_label: str
    created_at: str
    updated_at: str
    created_by: str
    created_by_source: str
    assigned_to: str
    asset: str
    notes_preview: str
    seconds_after_first: float
    in_burst: bool
    verdict: str
    worked: bool
    cannot_tell_because: list[str] = field(default_factory=list)
    work_signals: dict[str, Any] = field(default_factory=dict)
    is_suggested_keep: bool = False
    keep_reason: str = ""


@dataclass
class DuplicateGroup:
    """A (maintenance_item, due_date) group with more than one work order."""

    maintenance_item_id: str
    maintenance_item_title: str
    asset: str
    asset_id: str
    due_date: str
    count: int
    first_created_at: str
    last_created_at: str
    span_seconds: float
    largest_burst: int
    confidence: str
    confidence_reason: str
    worked_count: int
    unknown_count: int
    no_recorded_work_count: int
    suggested_keep_id: str
    suggested_keep_reason: str
    work_orders: list[WorkOrderRow] = field(default_factory=list)


@dataclass
class ReportScope:
    """How much of the picture the output actually shows.

    ``--limit`` and ``--min-confidence`` shrink the printed list, and a captain
    reading a truncated report would otherwise be told the damage is smaller
    than it is. Every count needed to say "showing X of Y" lives here.
    """

    total_group_count: int
    matching_group_count: int
    shown_group_count: int
    total_by_confidence: dict[str, int]
    shown_by_confidence: dict[str, int]
    hidden_by_min_confidence: int
    hidden_by_min_confidence_tally: dict[str, int]
    min_confidence: str
    dropped_by_limit: int
    limit: int | None

    @property
    def summary_line(self) -> str:
        return (
            f"Showing {self.shown_group_count} of {self.total_group_count} "
            "suspected group(s) found."
        )

    def notes(self) -> list[str]:
        """Human-readable lines explaining anything that was left out."""
        lines = [
            self.summary_line,
            (
                f"All {self.total_group_count} found: "
                f"{self.total_by_confidence['high']} high, "
                f"{self.total_by_confidence['medium']} medium, "
                f"{self.total_by_confidence['low']} low confidence."
            ),
        ]
        if self.hidden_by_min_confidence:
            hidden = self.hidden_by_min_confidence_tally
            lines.append(
                f"{self.hidden_by_min_confidence} group(s) hidden by "
                f"--min-confidence {self.min_confidence}: "
                f"{hidden['medium']} medium, {hidden['low']} low confidence."
            )
        if self.dropped_by_limit:
            lines.append(
                f"{self.dropped_by_limit} further group(s) not shown because of "
                f"--limit {self.limit}."
            )
        return lines


class Command(BaseCommand):
    help = (
        "Report SUSPECTED duplicate maintenance work orders left behind by the "
        "BACKEND-18 retry bug, grouped by (maintenance item, due date). "
        "DELIBERATELY READ-ONLY: it never calls save(), delete(), update() or any "
        "bulk write, and it has no cleanup, merge or --fix mode by design. "
        "Cleaning up duplicates is a separate human decision — do not add writes here."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--window-seconds",
            type=int,
            default=DEFAULT_WINDOW_SECONDS,
            help=(
                "Work orders created within this many seconds of each other count as "
                "one retry burst, which drives the confidence ranking. "
                f"Default: {DEFAULT_WINDOW_SECONDS}."
            ),
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help=(
                "Only report groups with at least one work order created on or after "
                "this date (YYYY-MM-DD). Whole groups are selected: every member is "
                "then reported, including members created before the cutoff, so the "
                "count and the 'earliest' designation stay truthful for a burst that "
                "straddles it. Useful for narrowing to the period the 500s were hit."
            ),
        )
        parser.add_argument(
            "--min-confidence",
            choices=["high", "medium", "low"],
            default="low",
            help=(
                "Only print groups at or above this confidence. Default 'low' (print "
                "everything) — low-confidence groups are the likely false positives. "
                "Anything hidden is counted in the output."
            ),
        )
        parser.add_argument(
            "--format",
            choices=["text", "json", "csv"],
            default="text",
            help="Output format. 'text' is human-readable; json/csv are for long lists.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=(
                "Print at most this many groups (highest confidence, most recent "
                "first). The totals reported always cover every group found, not "
                "just the printed ones."
            ),
        )

    def handle(self, *args, **options):
        window = timedelta(seconds=max(0, options["window_seconds"]))
        since = self._parse_since(options["since"])
        all_groups = self._collect_groups(window=window, since=since)

        min_confidence = options["min_confidence"]
        threshold = CONFIDENCE_ORDER[min_confidence]
        matching = [g for g in all_groups if CONFIDENCE_ORDER[g.confidence] <= threshold]
        hidden = [g for g in all_groups if CONFIDENCE_ORDER[g.confidence] > threshold]
        # Highest confidence first; most recent damage first inside each band.
        matching.sort(key=lambda g: g.last_created_at, reverse=True)
        matching.sort(key=lambda g: CONFIDENCE_ORDER[g.confidence])

        limit = options["limit"]
        groups = matching if limit is None else matching[: max(0, limit)]

        scope = ReportScope(
            total_group_count=len(all_groups),
            matching_group_count=len(matching),
            shown_group_count=len(groups),
            total_by_confidence=self._tally(all_groups),
            shown_by_confidence=self._tally(groups),
            hidden_by_min_confidence=len(hidden),
            hidden_by_min_confidence_tally=self._tally(hidden),
            min_confidence=min_confidence,
            dropped_by_limit=len(matching) - len(groups),
            limit=limit,
        )

        fmt = options["format"]
        if fmt == "json":
            self.stdout.write(self._render_json(groups, window=window, since=since, scope=scope))
        elif fmt == "csv":
            self.stdout.write(self._render_csv(groups, scope=scope))
        else:
            self._render_text(groups, window=window, since=since, scope=scope)

    # ---------------------------------------------------------------- helpers

    def _tally(self, groups):
        counts = {"high": 0, "medium": 0, "low": 0}
        for group in groups:
            counts[group.confidence] += 1
        return counts

    def _parse_since(self, raw):
        if not raw:
            return None
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError as exc:
            raise CommandError(f"--since must be YYYY-MM-DD, got {raw!r}") from exc
        return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

    def _base_queryset(self):
        # Corrective work orders carry no maintenance_item; grouping NULLs
        # together would bucket unrelated assets, so they are out of scope. The
        # BACKEND-18 path always sets maintenance_item.
        return WorkOrder.objects.filter(
            maintenance_item__isnull=False,
            due_date__isnull=False,
        )

    def _group_keys(self, base, since):
        """The ``(maintenance_item, due_date)`` pairs worth reporting.

        ``--since`` SELECTS whole groups rather than filtering individual rows:
        a group qualifies when any one of its members was created at or after
        the cutoff, and every member is then reported. Filtering row by row
        would shrink a burst that straddles the boundary and make both the
        count and the "earliest is the intended one" reasoning wrong.
        """
        keys = base.values("maintenance_item_id", "due_date").annotate(n=Count("id"))
        if since is not None:
            keys = keys.annotate(recent=Count("id", filter=Q(created_at__gte=since))).filter(
                recent__gt=0
            )
        return list(keys.filter(n__gt=1).order_by())

    def _candidates(self, base, pairs):
        """Fetch exactly the group members, with their evidence prefetched.

        Filtering on ``maintenance_item_id__in`` × ``due_date__in`` would be the
        cross product of the two key columns, not the set of duplicate pairs —
        and bulk generation stamps many different items with the same due date,
        so unrelated rows would be loaded with every prefetch attached and then
        thrown away. OR the exact pairs instead, in chunks so the SQL stays a
        sane size on a production-scale run.
        """
        rows = []
        for start in range(0, len(pairs), PAIR_CHUNK):
            chunk = pairs[start : start + PAIR_CHUNK]
            predicate = reduce(
                operator.or_,
                (Q(maintenance_item_id=item, due_date=due) for item, due in chunk),
            )
            rows.extend(
                base.filter(predicate)
                .select_related(
                    "maintenance_item", "maintenance_item__asset", "asset", "assigned_to"
                )
                .prefetch_related(
                    "task_completions",
                    "material_usage",
                    Prefetch(
                        "tools",
                        queryset=WorkOrderTool.objects.select_related("tool"),
                    ),
                    "loto_completions",
                    "maintenance_logs",
                    "photos",
                    "attachments",
                    "validations",
                    "submissions",
                    "omr_templates",
                    "additional_maintenance_items",
                    Prefetch(
                        "audit_events",
                        queryset=MaintenanceAuditEvent.objects.select_related("actor"),
                    ),
                )
                .order_by("created_at")
            )
        return rows

    def _collect_groups(self, *, window, since):
        base = self._base_queryset()
        keys = self._group_keys(base, since)
        if not keys:
            return []

        pairs = sorted(
            {(k["maintenance_item_id"], k["due_date"]) for k in keys},
            key=lambda pair: (str(pair[0]), pair[1]),
        )
        candidates = self._candidates(base, pairs)

        buckets: dict[tuple, list] = {}
        for wo in candidates:
            buckets.setdefault((wo.maintenance_item_id, wo.due_date), []).append(wo)

        return [
            self._build_group(work_orders, window=window)
            for work_orders in buckets.values()
            if len(work_orders) > 1
        ]

    def _creator(self, wo):
        """Who created this work order, per the maintenance audit log.

        ``generate_work_order`` — the BACKEND-18 path — writes **no** audit row,
        while the plain ``POST /api/inventory/work-orders/`` create path does.
        So a missing creator is itself weak evidence for the retry path (and is
        also expected for any row predating migration 0057).

        Read from the prefetched ``audit_events``, which the same fetch already
        needs for the "audit events other than wo_create" signal.
        """
        events = sorted(wo.audit_events.all(), key=lambda event: event.created_at)
        for event in events:
            if event.action == MaintenanceAuditEvent.Action.WO_CREATE:
                return str(event.actor) if event.actor_id else "system (no actor on audit row)"
        return ""

    def _tool_signals(self, tools):
        """Restaging evidence from the per-job tool rows.

        ``create_work_order_tools`` copies the template's ``location_hint`` and
        ``notes`` onto every generated row, so a NON-EMPTY value proves nothing.
        Only a DIVERGENCE from the template means a human restaged this tool for
        this job. When the template row is gone the comparison cannot be made at
        all — that is indeterminate, not absence.
        """
        restaged = 0
        unverifiable = 0
        for row in tools:
            if row.is_ad_hoc:
                continue
            template = row.tool
            if template is None:
                unverifiable += 1
                continue
            copied = (template.location_hint or "", template.notes or "")
            staged = (row.location_hint or "", row.notes or "")
            if staged != copied:
                restaged += 1
        return restaged, unverifiable

    def _work_signals(self, wo, now):
        task_completions = list(wo.task_completions.all())
        usage = list(wo.material_usage.all())
        tools = list(wo.tools.all())
        loto = list(wo.loto_completions.all())
        audit_events = list(wo.audit_events.all())

        tools_restaged, tools_unverifiable = self._tool_signals(tools)
        loto_notes = sum(1 for row in loto if (row.notes or "").strip())
        if (wo.loto_completion_note or "").strip():
            loto_notes += 1
        edited = (wo.updated_at - wo.created_at).total_seconds() > UPDATED_SLACK_SECONDS
        signals = {
            "status": wo.status,
            "status_beyond_open": wo.status != WorkOrder.Status.OPEN,
            "tasks_completed": sum(1 for t in task_completions if t.is_completed),
            "tasks_total": len(task_completions),
            "task_notes": sum(1 for t in task_completions if (t.notes or "").strip()),
            "task_timers": sum(
                1 for t in task_completions if (t.elapsed_seconds or 0) > 0 or t.is_timing
            ),
            "materials_used": sum(1 for m in usage if m.was_used),
            "materials_applied": sum(1 for m in usage if m.applied_quantity is not None),
            "qty_edited": sum(
                1
                for m in usage
                if m.quantity_used is not None and m.quantity_used != m.quantity_planned
            ),
            "adhoc_materials": sum(1 for m in usage if m.is_ad_hoc),
            "material_evidence": sum(
                1
                for m in usage
                if m.receipt_image
                or m.unit_cost is not None
                or m.purchase_order_item_id is not None
            ),
            "materials_total": len(usage),
            "adhoc_tools": sum(1 for t in tools if t.is_ad_hoc),
            "tools_restaged": tools_restaged,
            "tools_unverifiable": tools_unverifiable,
            "tools_total": len(tools),
            "loto_completed": sum(1 for row in loto if row.is_completed),
            "loto_notes": loto_notes,
            "loto_total": len(loto),
            "time_seconds": wo.live_elapsed_seconds(now=now),
            "started_at": wo.started_at.isoformat() if wo.started_at else "",
            "completed_at": wo.completed_at.isoformat() if wo.completed_at else "",
            "completed_by": wo.completed_by_name or "",
            "completed_scan": bool(wo.completed_scan),
            "bundled_items": len(wo.additional_maintenance_items.all()),
            "maintenance_logs": len(wo.maintenance_logs.all()),
            "photos": len(wo.photos.all()),
            "attachments": len(wo.attachments.all()),
            "validations": len(wo.validations.all()),
            "submissions": len(wo.submissions.all()),
            "edited_since_create": edited,
            "assigned_to": bool(wo.assigned_to_id),
            "omr_templates": len(wo.omr_templates.all()),
            "other_audit_events": sum(
                1
                for event in audit_events
                if event.action != MaintenanceAuditEvent.Action.WO_CREATE
            ),
        }
        verdict, indeterminate = self._verdict(wo, signals)
        return verdict, indeterminate, signals

    def _verdict(self, wo, signals):
        """Three-state answer: worked, cannot tell, or nothing recorded.

        A boolean here would flatten "we checked every signal and found nothing"
        into the same answer as "the data cannot tell us", and only the first of
        those makes a work order safe to remove. Absence of a signal nobody
        checked is not evidence of absence.
        """
        definitive = (
            signals["status_beyond_open"],
            signals["tasks_completed"],
            signals["task_notes"],
            signals["task_timers"],
            signals["materials_used"],
            signals["materials_applied"],
            signals["qty_edited"],
            signals["adhoc_materials"],
            signals["material_evidence"],
            signals["adhoc_tools"],
            signals["tools_restaged"],
            signals["loto_completed"],
            signals["loto_notes"],
            signals["time_seconds"],
            signals["started_at"],
            signals["completed_at"],
            signals["completed_by"],
            signals["completed_scan"],
            signals["bundled_items"],
            signals["maintenance_logs"],
            signals["photos"],
            signals["attachments"],
            signals["validations"],
            signals["submissions"],
            signals["edited_since_create"],
        )
        if any(definitive):
            return VERDICT_WORKED, []

        indeterminate = []
        if signals["assigned_to"]:
            indeterminate.append(
                f"assigned to {wo.assigned_to} — somebody was put on this job, so what "
                "they did may simply not have been recorded here"
            )
        if signals["omr_templates"]:
            indeterminate.append(
                "a printed OMR form was generated for it, so a paper copy exists that "
                "may have been worked and never scanned back"
            )
        if signals["other_audit_events"]:
            indeterminate.append(
                f"{signals['other_audit_events']} audit event(s) other than wo_create "
                "are recorded against it"
            )
        if signals["tools_unverifiable"]:
            indeterminate.append(
                f"{signals['tools_unverifiable']} tool row(s) lost the template row they "
                "were copied from, so a per-job restage cannot be told apart from the "
                "copied-in value"
            )
        if indeterminate:
            return VERDICT_UNKNOWN, indeterminate
        return VERDICT_NONE, []

    def _largest_burst(self, work_orders, window):
        """Biggest run of rows created within ``window`` of each other, and who is in it.

        Returns the run size and the ids of its members, so ``in_burst`` on each
        row agrees with the number the confidence ranking is derived from. A
        run of one is not a burst, so it names nobody.
        """
        stamps = [wo.created_at for wo in work_orders]
        best_start = best_end = 0
        start = 0
        for end in range(len(stamps)):
            while stamps[end] - stamps[start] > window:
                start += 1
            if end - start > best_end - best_start:
                best_start, best_end = start, end
        size = best_end - best_start + 1
        if size < 2:
            return size, frozenset()
        return size, frozenset(str(wo.id) for wo in work_orders[best_start : best_end + 1])

    def _build_group(self, work_orders, *, window):
        now = timezone.now()
        first = work_orders[0]
        item = first.maintenance_item
        burst, burst_members = self._largest_burst(work_orders, window)
        span = (work_orders[-1].created_at - first.created_at).total_seconds()

        rows = []
        for wo in work_orders:
            verdict, indeterminate, signals = self._work_signals(wo, now)
            created_by = self._creator(wo)
            delta = (wo.created_at - first.created_at).total_seconds()
            rows.append(
                WorkOrderRow(
                    id=str(wo.id),
                    number=wo.short_id,
                    status=wo.status,
                    status_label=wo.get_status_display(),
                    created_at=wo.created_at.isoformat(),
                    updated_at=wo.updated_at.isoformat(),
                    created_by=created_by,
                    created_by_source=(
                        "maintenance audit log"
                        if created_by
                        else "not recorded (generate_work_order writes no audit row)"
                    ),
                    assigned_to=str(wo.assigned_to) if wo.assigned_to_id else "",
                    asset=str(wo.asset) if wo.asset_id else "",
                    notes_preview=(wo.notes or "").strip().replace("\n", " ")[:80],
                    seconds_after_first=delta,
                    in_burst=str(wo.id) in burst_members,
                    verdict=verdict,
                    worked=verdict == VERDICT_WORKED,
                    cannot_tell_because=indeterminate,
                    work_signals=signals,
                )
            )

        audited = sum(1 for row in rows if row.created_by)
        confidence, reason = self._rank(
            rows, burst=burst, span=span, audited=audited, window=window
        )
        keep_id, keep_reason = self._suggest_keep(rows, confidence=confidence)
        for row in rows:
            if row.id == keep_id:
                row.is_suggested_keep = True
                row.keep_reason = keep_reason

        return DuplicateGroup(
            maintenance_item_id=str(item.id),
            maintenance_item_title=item.title,
            asset=(
                str(item.asset) if item.asset_id else (str(first.asset) if first.asset_id else "")
            ),
            asset_id=str(item.asset_id or first.asset_id or ""),
            due_date=first.due_date.isoformat(),
            count=len(rows),
            first_created_at=rows[0].created_at,
            last_created_at=rows[-1].created_at,
            span_seconds=span,
            largest_burst=burst,
            confidence=confidence,
            confidence_reason=reason,
            worked_count=sum(1 for r in rows if r.verdict == VERDICT_WORKED),
            unknown_count=sum(1 for r in rows if r.verdict == VERDICT_UNKNOWN),
            no_recorded_work_count=sum(1 for r in rows if r.verdict == VERDICT_NONE),
            suggested_keep_id=keep_id,
            suggested_keep_reason=keep_reason,
            work_orders=rows,
        )

    def _rank(self, rows, *, burst, span, audited, window):
        """Rank confidence that a group is retry damage rather than intentional.

        Timestamp clustering is the primary signal: the BACKEND-18 retries were
        an operator pressing the key again after a 500, seconds apart. Absence of
        a WO_CREATE audit row is a corroborating signal, because
        ``generate_work_order`` — the buggy path — never wrote one.
        """
        window_s = int(window.total_seconds())
        if burst < 2:
            return (
                "low",
                (
                    f"no burst: no two work orders were created within {window_s}s of each "
                    f"other (spread over {self._duration(span)}). Most likely a legitimately "
                    "repeated or re-scheduled PM, not retry damage."
                ),
            )
        if audited == 0:
            return (
                "high",
                (
                    f"{burst} work orders created within {window_s}s of each other, and none "
                    "has a WO_CREATE audit row — the signature of the generate_work_order "
                    "path, which committed the row and then 500ed."
                ),
            )
        return (
            "medium",
            (
                f"{burst} work orders created within {window_s}s of each other, but {audited} "
                "of them carry a WO_CREATE audit row, so at least some were raised through "
                "the audited create path rather than the buggy one."
            ),
        )

    def _suggest_keep(self, rows, *, confidence):
        """Which row is most likely the 'intended' one — reasoning stated, not assumed.

        "Nothing is lost either way" is the sentence that authorises a deletion,
        so it is only ever printed when every row in the group came back
        NO-RECORDED-WORK. One row we merely cannot read is enough to withdraw it.
        """
        worked = [r for r in rows if r.verdict == VERDICT_WORKED]
        unknown = [r for r in rows if r.verdict == VERDICT_UNKNOWN]
        unknown_tail = (
            ""
            if not unknown
            else (
                f" {len(unknown)} row(s) here CANNOT BE SHOWN to be untouched — check "
                "those by hand before removing anything."
            )
        )

        if len(worked) > 1:
            newest = max(worked, key=lambda r: r.created_at)
            return (
                newest.id,
                (
                    f"AMBIGUOUS — {len(worked)} of these have been worked, so real work "
                    "would be lost whichever is dropped. The most recently created worked "
                    "row is named only as a starting point; reconcile these by hand."
                    + unknown_tail
                ),
            )
        if len(worked) == 1 and not unknown:
            return (
                worked[0].id,
                (
                    "only work order in this group that shows any sign of having been "
                    "worked; the others show no recorded work in any signal this report "
                    "checks"
                ),
            )
        if len(worked) == 1:
            return (
                worked[0].id,
                (
                    "the only work order here that shows recorded work, but the rest are "
                    "not all clear:" + unknown_tail
                ),
            )
        if unknown:
            return (
                rows[0].id,
                (
                    "none of these shows recorded work, but it is NOT true that nothing "
                    "would be lost:"
                    + unknown_tail
                    + " The earliest is named for reference only"
                ),
            )
        if confidence == "low":
            return (
                rows[0].id,
                (
                    "none of these has been worked, but there is no retry burst here "
                    "either, so these are more likely two intentional work orders than "
                    "duplicates. The earliest is named for reference only — check the "
                    "maintenance plan before treating either as spurious"
                ),
            )
        return (
            rows[0].id,
            (
                "none of these shows recorded work in any signal this report checks, so "
                "nothing recorded is lost either way. The earliest is the one the operator "
                "actually asked for — the later rows exist only because the 500 hid the "
                "first success and they retried. (Work done on paper and never scanned "
                "back leaves no trace here — see the coverage note.)"
            ),
        )

    def _duration(self, seconds):
        seconds = int(seconds)
        if seconds < 90:
            return f"{seconds}s"
        if seconds < 5400:
            return f"{seconds / 60:.1f} min"
        if seconds < 172800:
            return f"{seconds / 3600:.1f} h"
        return f"{seconds / 86400:.1f} days"

    # ---------------------------------------------------------------- output

    def _verdict_line(self, row):
        if row.verdict == VERDICT_WORKED:
            return "YES"
        if row.verdict == VERDICT_UNKNOWN:
            return "CANNOT TELL — " + "; ".join(row.cannot_tell_because)
        return "no recorded work in the signals checked"

    def _render_text(self, groups, *, window, since, scope):
        w = self.stdout.write
        w("=" * 78)
        w("SUSPECTED duplicate maintenance work orders (BACKEND-18 retry damage)")
        w("=" * 78)
        w("")
        w("READ-ONLY REPORT. This command changes nothing: it makes no writes of any")
        w("kind and has no cleanup mode. Deciding what to do with these rows is a")
        w("separate, human call.")
        w("")
        w(FALSE_POSITIVE_NOTE)
        w("")
        w(SIGNALS_NOTE)
        w("")
        w(COVERAGE_NOTE)
        w("")
        w(f"Burst window: {int(window.total_seconds())}s")
        if since is not None:
            w(
                "Only groups with at least one work order created on or after "
                f"{since.date().isoformat()} (whole groups, every member listed)"
            )
        w("")

        if not scope.total_group_count:
            w("No (maintenance item, due date) pair has more than one work order.")
            w("Nothing to review.")
            return

        for line in scope.notes():
            w(line)
        if scope.shown_group_count != scope.total_group_count:
            shown = scope.shown_by_confidence
            w(
                f"Shown here: {shown['high']} high, {shown['medium']} medium, "
                f"{shown['low']} low confidence."
            )
        w("")

        if not groups:
            w("Nothing left to print after the filters above.")
            return

        for index, group in enumerate(groups, start=1):
            w("-" * 78)
            w(f"[{index}/{scope.shown_group_count}] {group.confidence.upper()} confidence")
            w(f"  PM item      : {group.maintenance_item_title}  ({group.maintenance_item_id})")
            w(f"  Asset        : {group.asset or '(none recorded)'}")
            w(f"  Due date     : {group.due_date}   — shared by {group.count} work orders")
            w(
                f"  Created span : {self._duration(group.span_seconds)}"
                f"  (largest burst: {group.largest_burst} of {group.count})"
            )
            w(f"  Why ranked   : {group.confidence_reason}")
            w(
                f"  Worked       : {group.worked_count} of {group.count} show recorded work, "
                f"{group.unknown_count} cannot be told either way, "
                f"{group.no_recorded_work_count} show none"
            )
            w("")
            for row in group.work_orders:
                marker = ">>" if row.is_suggested_keep else "  "
                w(f"{marker} {row.number}   {row.id}")
                w(f"     status      : {row.status_label} ({row.status})")
                w(
                    f"     created     : {row.created_at}"
                    + (
                        ""
                        if row.seconds_after_first == 0
                        else f"  (+{self._duration(row.seconds_after_first)} after the first)"
                    )
                )
                w(f"     created by  : {row.created_by or row.created_by_source}")
                if row.assigned_to:
                    w(f"     assigned to : {row.assigned_to}")
                if row.notes_preview:
                    w(f"     notes       : {row.notes_preview}")
                w(f"     WORKED?     : {self._verdict_line(row)}")
                w(f"     signals     : {self._signal_line(row.work_signals)}")
                if row.is_suggested_keep:
                    w(f"     LIKELY INTENDED: {row.keep_reason}")
                w("")
        w("-" * 78)
        w("Reminder: suspected, not confirmed. Nothing above has been changed.")

    def _signal_line(self, s):
        return ", ".join(
            [
                f"tasks {s['tasks_completed']}/{s['tasks_total']} done",
                f"task notes {s['task_notes']}",
                f"task timers {s['task_timers']}",
                f"materials used {s['materials_used']}/{s['materials_total']}",
                f"stock applied {s['materials_applied']}",
                f"qty edited {s['qty_edited']}",
                f"ad-hoc materials {s['adhoc_materials']}",
                f"material evidence {s['material_evidence']}",
                f"tools {s['tools_total']} (ad-hoc {s['adhoc_tools']}, "
                f"restaged {s['tools_restaged']}, unverifiable {s['tools_unverifiable']})",
                f"loto {s['loto_completed']}/{s['loto_total']} done",
                f"loto notes {s['loto_notes']}",
                f"time {s['time_seconds']}s",
                f"started {s['started_at'] or '-'}",
                f"completed {s['completed_at'] or '-'}",
                f"completed_by {s['completed_by'] or '-'}",
                f"scan {'yes' if s['completed_scan'] else 'no'}",
                f"bundled items {s['bundled_items']}",
                f"logs {s['maintenance_logs']}",
                f"photos {s['photos']}",
                f"attachments {s['attachments']}",
                f"validations {s['validations']}",
                f"submissions {s['submissions']}",
                f"edited_since_create {'yes' if s['edited_since_create'] else 'no'}",
                f"assigned {'yes' if s['assigned_to'] else 'no'}",
                f"omr templates {s['omr_templates']}",
                f"other audit events {s['other_audit_events']}",
            ]
        )

    def _render_json(self, groups, *, window, since, scope):
        payload = {
            "read_only": True,
            "report": "suspected duplicate maintenance work orders (BACKEND-18)",
            "suspected_not_confirmed": True,
            "false_positive_causes": FALSE_POSITIVE_NOTE,
            "worked_signal_definitions": SIGNALS_NOTE,
            "coverage_caveat": COVERAGE_NOTE,
            "burst_window_seconds": int(window.total_seconds()),
            "since": since.date().isoformat() if since else None,
            "since_selects_whole_groups": True,
            "total_group_count": scope.total_group_count,
            "matching_group_count": scope.matching_group_count,
            "group_count": scope.shown_group_count,
            "confidence_tally_all": scope.total_by_confidence,
            "confidence_tally_shown": scope.shown_by_confidence,
            "min_confidence": scope.min_confidence,
            "groups_hidden_by_min_confidence": scope.hidden_by_min_confidence,
            "groups_hidden_by_min_confidence_tally": scope.hidden_by_min_confidence_tally,
            "limit": scope.limit,
            "groups_not_shown_due_to_limit": scope.dropped_by_limit,
            "scope_note": " ".join(scope.notes()),
            "groups": [asdict(group) for group in groups],
        }
        return json.dumps(payload, indent=2, sort_keys=False)

    def _render_csv(self, groups, *, scope):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "# SUSPECTED duplicates only — read-only report, nothing was changed. "
                "False positives: a legitimately re-scheduled or deliberately repeated "
                "PM shares (maintenance_item, due_date) too; low-confidence rows are "
                "the likely ones."
            ]
        )
        writer.writerow(["# " + " ".join(scope.notes())])
        writer.writerow(["# " + " ".join(COVERAGE_NOTE.split())])
        writer.writerow(
            [
                "confidence",
                "confidence_reason",
                "maintenance_item_id",
                "maintenance_item",
                "asset",
                "due_date",
                "group_size",
                "largest_burst",
                "group_span_seconds",
                "work_order_id",
                "work_order_number",
                "status",
                "created_at",
                "created_by",
                "created_by_source",
                "assigned_to",
                "seconds_after_first",
                "in_burst",
                "verdict",
                "worked",
                "cannot_tell_because",
                "tasks_completed",
                "tasks_total",
                "task_notes",
                "task_timers",
                "materials_used",
                "materials_applied",
                "qty_edited",
                "adhoc_materials",
                "material_evidence",
                "adhoc_tools",
                "tools_restaged",
                "tools_unverifiable",
                "loto_completed",
                "loto_notes",
                "time_seconds",
                "started_at",
                "completed_at",
                "completed_by",
                "completed_scan",
                "bundled_items",
                "maintenance_logs",
                "photos",
                "attachments",
                "validations",
                "submissions",
                "edited_since_create",
                "omr_templates",
                "other_audit_events",
                "suggested_keep",
                "suggested_keep_reason",
            ]
        )
        for group in groups:
            for row in group.work_orders:
                s = row.work_signals
                writer.writerow(
                    [
                        group.confidence,
                        group.confidence_reason,
                        group.maintenance_item_id,
                        group.maintenance_item_title,
                        group.asset,
                        group.due_date,
                        group.count,
                        group.largest_burst,
                        int(group.span_seconds),
                        row.id,
                        row.number,
                        row.status,
                        row.created_at,
                        row.created_by,
                        row.created_by_source,
                        row.assigned_to,
                        int(row.seconds_after_first),
                        "yes" if row.in_burst else "no",
                        row.verdict,
                        "yes" if row.worked else "no",
                        "; ".join(row.cannot_tell_because),
                        s["tasks_completed"],
                        s["tasks_total"],
                        s["task_notes"],
                        s["task_timers"],
                        s["materials_used"],
                        s["materials_applied"],
                        s["qty_edited"],
                        s["adhoc_materials"],
                        s["material_evidence"],
                        s["adhoc_tools"],
                        s["tools_restaged"],
                        s["tools_unverifiable"],
                        s["loto_completed"],
                        s["loto_notes"],
                        s["time_seconds"],
                        s["started_at"],
                        s["completed_at"],
                        s["completed_by"],
                        "yes" if s["completed_scan"] else "no",
                        s["bundled_items"],
                        s["maintenance_logs"],
                        s["photos"],
                        s["attachments"],
                        s["validations"],
                        s["submissions"],
                        "yes" if s["edited_since_create"] else "no",
                        s["omr_templates"],
                        s["other_audit_events"],
                        "yes" if row.is_suggested_keep else "no",
                        row.keep_reason,
                    ]
                )
        return buf.getvalue()
