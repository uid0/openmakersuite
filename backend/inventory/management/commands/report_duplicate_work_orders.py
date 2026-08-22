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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from inventory.models import MaintenanceAuditEvent, WorkOrder

#: Rows created within this many seconds of each other are treated as one
#: burst. The BACKEND-18 retries landed seconds apart (an operator re-pressing
#: "w" after a 500), so a tight burst is the strongest available evidence that a
#: group is retry damage rather than deliberately repeated work.
DEFAULT_WINDOW_SECONDS = 300

#: ``updated_at`` is set by ``auto_now`` on the same ``save()`` that sets
#: ``created_at``, so the two differ by microseconds on an untouched row. Only
#: treat a row as "edited since creation" beyond this slack.
UPDATED_SLACK_SECONDS = 2

CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}

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
  materials_used      WorkOrderMaterialUsage rows with was_used=True. NOTE:
                      quantity_used alone is NOT evidence — generation pre-fills
                      it from the template's planned quantity, so a non-zero
                      quantity_used is present on a work order nobody touched.
  materials_applied   usage rows with applied_quantity set, i.e. stock was
                      actually decremented against this work order. This is the
                      hardest material signal: it has an inventory side effect.
  qty_edited          usage rows whose quantity_used differs from
                      quantity_planned, i.e. somebody changed the number.
  loto_completed      WorkOrderLotoCompletion rows with is_completed=True.
  time_seconds        stopwatch time on the job (elapsed + any running segment).
  started_at          first time the timer was started.
  completed_at        set when the work order was marked complete.
  completed_by        completed_by_name from a scanned paper form.
  maintenance_logs    MaintenanceLog rows written back from this work order.
  photos/attachments  evidence uploaded against the work order.
  validations         WorkOrderValidation sign-offs.
  submissions         scanned/emailed-in paper submissions.
  edited_since_create updated_at moved after created_at (someone saved the row).

A work order is called WORKED if any of those fire. The retry artifacts left by
BACKEND-18 were never opened by anyone, so they should show none."""


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
    worked: bool
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
    suggested_keep_id: str
    suggested_keep_reason: str
    work_orders: list[WorkOrderRow] = field(default_factory=list)


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
                "Only consider work orders created on or after this date (YYYY-MM-DD). "
                "Useful for narrowing to the period the 500s were being hit."
            ),
        )
        parser.add_argument(
            "--min-confidence",
            choices=["high", "medium", "low"],
            default="low",
            help=(
                "Only print groups at or above this confidence. Default 'low' (print "
                "everything) — low-confidence groups are the likely false positives."
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
            help="Print at most this many groups (highest confidence, most recent first).",
        )

    def handle(self, *args, **options):
        window = timedelta(seconds=max(0, options["window_seconds"]))
        since = self._parse_since(options["since"])
        groups = self._collect_groups(window=window, since=since)

        threshold = CONFIDENCE_ORDER[options["min_confidence"]]
        groups = [g for g in groups if CONFIDENCE_ORDER[g.confidence] <= threshold]
        # Highest confidence first; most recent damage first inside each band.
        groups.sort(key=lambda g: g.last_created_at, reverse=True)
        groups.sort(key=lambda g: CONFIDENCE_ORDER[g.confidence])
        if options["limit"] is not None:
            groups = groups[: max(0, options["limit"])]

        fmt = options["format"]
        if fmt == "json":
            self.stdout.write(self._render_json(groups, window=window, since=since))
        elif fmt == "csv":
            self.stdout.write(self._render_csv(groups))
        else:
            self._render_text(groups, window=window, since=since)

    # ---------------------------------------------------------------- helpers

    def _parse_since(self, raw):
        if not raw:
            return None
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError as exc:
            raise CommandError(f"--since must be YYYY-MM-DD, got {raw!r}") from exc
        return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

    def _base_queryset(self, since):
        # Corrective work orders carry no maintenance_item; grouping NULLs
        # together would bucket unrelated assets, so they are out of scope. The
        # BACKEND-18 path always sets maintenance_item.
        qs = WorkOrder.objects.filter(
            maintenance_item__isnull=False,
            due_date__isnull=False,
        )
        if since is not None:
            qs = qs.filter(created_at__gte=since)
        return qs

    def _collect_groups(self, *, window, since):
        base = self._base_queryset(since)
        keys = (
            base.values("maintenance_item_id", "due_date")
            .annotate(n=Count("id"))
            .filter(n__gt=1)
            .order_by()
        )
        keys = list(keys)
        if not keys:
            return []

        candidates = list(
            base.filter(
                maintenance_item_id__in={k["maintenance_item_id"] for k in keys},
                due_date__in={k["due_date"] for k in keys},
            )
            .select_related("maintenance_item", "maintenance_item__asset", "asset", "assigned_to")
            .prefetch_related(
                "task_completions",
                "material_usage",
                "loto_completions",
                "maintenance_logs",
                "photos",
                "attachments",
                "validations",
                "submissions",
            )
            .order_by("created_at")
        )

        wanted = {(k["maintenance_item_id"], k["due_date"]) for k in keys}
        buckets: dict[tuple, list] = {}
        for wo in candidates:
            key = (wo.maintenance_item_id, wo.due_date)
            if key in wanted:
                buckets.setdefault(key, []).append(wo)

        creators = self._creator_map([wo.id for wo in candidates])

        return [
            self._build_group(work_orders, window=window, creators=creators)
            for work_orders in buckets.values()
            if len(work_orders) > 1
        ]

    def _creator_map(self, work_order_ids):
        """Who created each work order, per the maintenance audit log.

        ``generate_work_order`` — the BACKEND-18 path — writes **no** audit row,
        while the plain ``POST /api/inventory/work-orders/`` create path does.
        So a missing creator is itself weak evidence for the retry path (and is
        also expected for any row predating migration 0057).
        """
        events = (
            MaintenanceAuditEvent.objects.filter(
                action=MaintenanceAuditEvent.Action.WO_CREATE,
                work_order_id__in=list(work_order_ids),
            )
            .select_related("actor")
            .order_by("created_at")
        )
        out = {}
        for event in events:
            out.setdefault(
                event.work_order_id,
                str(event.actor) if event.actor_id else "system (no actor on audit row)",
            )
        return out

    def _work_signals(self, wo, now):
        task_completions = list(wo.task_completions.all())
        usage = list(wo.material_usage.all())
        loto = list(wo.loto_completions.all())

        edited = (wo.updated_at - wo.created_at).total_seconds() > UPDATED_SLACK_SECONDS
        signals = {
            "status": wo.status,
            "status_beyond_open": wo.status != WorkOrder.Status.OPEN,
            "tasks_completed": sum(1 for t in task_completions if t.is_completed),
            "tasks_total": len(task_completions),
            "materials_used": sum(1 for m in usage if m.was_used),
            "materials_applied": sum(1 for m in usage if m.applied_quantity is not None),
            "qty_edited": sum(
                1
                for m in usage
                if m.quantity_used is not None and m.quantity_used != m.quantity_planned
            ),
            "materials_total": len(usage),
            "loto_completed": sum(1 for row in loto if row.is_completed),
            "loto_total": len(loto),
            "time_seconds": wo.live_elapsed_seconds(now=now),
            "started_at": wo.started_at.isoformat() if wo.started_at else "",
            "completed_at": wo.completed_at.isoformat() if wo.completed_at else "",
            "completed_by": wo.completed_by_name or "",
            "maintenance_logs": len(wo.maintenance_logs.all()),
            "photos": len(wo.photos.all()),
            "attachments": len(wo.attachments.all()),
            "validations": len(wo.validations.all()),
            "submissions": len(wo.submissions.all()),
            "edited_since_create": edited,
        }
        worked = any(
            (
                signals["status_beyond_open"],
                signals["tasks_completed"],
                signals["materials_used"],
                signals["materials_applied"],
                signals["qty_edited"],
                signals["loto_completed"],
                signals["time_seconds"],
                signals["started_at"],
                signals["completed_at"],
                signals["completed_by"],
                signals["maintenance_logs"],
                signals["photos"],
                signals["attachments"],
                signals["validations"],
                signals["submissions"],
                signals["edited_since_create"],
            )
        )
        return worked, signals

    def _largest_burst(self, work_orders, window):
        """Size of the biggest run of rows created within ``window`` of each other."""
        best = 1
        stamps = [wo.created_at for wo in work_orders]
        start = 0
        for end in range(len(stamps)):
            while stamps[end] - stamps[start] > window:
                start += 1
            best = max(best, end - start + 1)
        return best

    def _build_group(self, work_orders, *, window, creators):
        now = timezone.now()
        first = work_orders[0]
        item = first.maintenance_item
        burst = self._largest_burst(work_orders, window)
        span = (work_orders[-1].created_at - first.created_at).total_seconds()
        audited = sum(1 for wo in work_orders if wo.id in creators)

        rows = []
        for wo in work_orders:
            worked, signals = self._work_signals(wo, now)
            delta = (wo.created_at - first.created_at).total_seconds()
            rows.append(
                WorkOrderRow(
                    id=str(wo.id),
                    number=self._number(wo),
                    status=wo.status,
                    status_label=wo.get_status_display(),
                    created_at=wo.created_at.isoformat(),
                    updated_at=wo.updated_at.isoformat(),
                    created_by=creators.get(wo.id, ""),
                    created_by_source=(
                        "maintenance audit log"
                        if wo.id in creators
                        else "not recorded (generate_work_order writes no audit row)"
                    ),
                    assigned_to=str(wo.assigned_to) if wo.assigned_to_id else "",
                    asset=str(wo.asset) if wo.asset_id else "",
                    notes_preview=(wo.notes or "").strip().replace("\n", " ")[:80],
                    seconds_after_first=delta,
                    in_burst=delta <= window.total_seconds(),
                    worked=worked,
                    work_signals=signals,
                )
            )

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
            worked_count=sum(1 for r in rows if r.worked),
            suggested_keep_id=keep_id,
            suggested_keep_reason=keep_reason,
            work_orders=rows,
        )

    def _number(self, wo):
        """A human-quotable identifier.

        ``WorkOrder`` has no sequence number column — the UUID primary key *is*
        the number everywhere else in the system — so quote the short form and
        keep the full UUID alongside it.
        """
        return f"WO-{str(wo.id)[:8]}"

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
        """Which row is most likely the 'intended' one — reasoning stated, not assumed."""
        worked = [r for r in rows if r.worked]
        if len(worked) == 1:
            return (
                worked[0].id,
                (
                    "only work order in this group that shows any sign of having been "
                    "worked; the others carry no completions, no time and no evidence"
                ),
            )
        if len(worked) > 1:
            newest = max(worked, key=lambda r: r.created_at)
            return (
                newest.id,
                (
                    f"AMBIGUOUS — {len(worked)} of these have been worked, so real work "
                    "would be lost whichever is dropped. The most recently created worked "
                    "row is named only as a starting point; reconcile these by hand"
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
                "none of these has been worked, so nothing is lost either way. The "
                "earliest is the one the operator actually asked for — the later rows "
                "exist only because the 500 hid the first success and they retried"
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

    def _render_text(self, groups, *, window, since):
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
        w(f"Burst window: {int(window.total_seconds())}s")
        if since is not None:
            w(f"Only work orders created on or after: {since.date().isoformat()}")
        w("")

        if not groups:
            w("No (maintenance item, due date) pair has more than one work order.")
            w("Nothing to review.")
            return

        by_conf = {"high": 0, "medium": 0, "low": 0}
        for group in groups:
            by_conf[group.confidence] += 1
        w(
            f"{len(groups)} suspected group(s): "
            f"{by_conf['high']} high, {by_conf['medium']} medium, {by_conf['low']} low confidence."
        )
        w("")

        for index, group in enumerate(groups, start=1):
            w("-" * 78)
            w(f"[{index}/{len(groups)}] {group.confidence.upper()} confidence")
            w(f"  PM item      : {group.maintenance_item_title}  ({group.maintenance_item_id})")
            w(f"  Asset        : {group.asset or '(none recorded)'}")
            w(f"  Due date     : {group.due_date}   — shared by {group.count} work orders")
            w(
                f"  Created span : {self._duration(group.span_seconds)}"
                f"  (largest burst: {group.largest_burst} of {group.count})"
            )
            w(f"  Why ranked   : {group.confidence_reason}")
            w(f"  Worked       : {group.worked_count} of {group.count} show signs of real work")
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
                w(f"     WORKED?     : {'YES' if row.worked else 'no evidence of work'}")
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
                f"materials used {s['materials_used']}/{s['materials_total']}",
                f"stock applied {s['materials_applied']}",
                f"qty edited {s['qty_edited']}",
                f"loto {s['loto_completed']}/{s['loto_total']} done",
                f"time {s['time_seconds']}s",
                f"started {s['started_at'] or '-'}",
                f"completed {s['completed_at'] or '-'}",
                f"completed_by {s['completed_by'] or '-'}",
                f"logs {s['maintenance_logs']}",
                f"photos {s['photos']}",
                f"attachments {s['attachments']}",
                f"validations {s['validations']}",
                f"submissions {s['submissions']}",
                f"edited_since_create {'yes' if s['edited_since_create'] else 'no'}",
            ]
        )

    def _render_json(self, groups, *, window, since):
        payload = {
            "read_only": True,
            "report": "suspected duplicate maintenance work orders (BACKEND-18)",
            "suspected_not_confirmed": True,
            "false_positive_causes": FALSE_POSITIVE_NOTE,
            "worked_signal_definitions": SIGNALS_NOTE,
            "burst_window_seconds": int(window.total_seconds()),
            "since": since.date().isoformat() if since else None,
            "group_count": len(groups),
            "groups": [asdict(group) for group in groups],
        }
        return json.dumps(payload, indent=2, sort_keys=False)

    def _render_csv(self, groups):
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
                "worked",
                "tasks_completed",
                "tasks_total",
                "materials_used",
                "materials_applied",
                "qty_edited",
                "loto_completed",
                "time_seconds",
                "started_at",
                "completed_at",
                "completed_by",
                "maintenance_logs",
                "photos",
                "attachments",
                "validations",
                "submissions",
                "edited_since_create",
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
                        "yes" if row.worked else "no",
                        s["tasks_completed"],
                        s["tasks_total"],
                        s["materials_used"],
                        s["materials_applied"],
                        s["qty_edited"],
                        s["loto_completed"],
                        s["time_seconds"],
                        s["started_at"],
                        s["completed_at"],
                        s["completed_by"],
                        s["maintenance_logs"],
                        s["photos"],
                        s["attachments"],
                        s["validations"],
                        s["submissions"],
                        "yes" if s["edited_since_create"] else "no",
                        "yes" if row.is_suggested_keep else "no",
                        row.keep_reason,
                    ]
                )
        return buf.getvalue()
