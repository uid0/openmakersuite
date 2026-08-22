"""Tests for the read-only ``report_duplicate_work_orders`` management command.

The seeded data deliberately contains all four shapes the captain needs the
report to tell apart:

* a real BACKEND-18 retry cluster (three work orders seconds apart, untouched);
* a cluster where one of the duplicates has since been worked;
* a legitimately repeated PM raised twice, days apart, through the audited
  create path — which shares the (maintenance_item, due_date) grouping key and
  must therefore be flagged as a likely false positive rather than hidden;
* a cluster where the only trace of work on one duplicate is an ad-hoc material
  row carrying a receipt — nothing on the work order row itself moved, which is
  exactly the case a WorkOrder-only signal set calls "untouched".
"""

import ast
import json
import pathlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

import pytest

from inventory.models import (
    MaintenanceAuditEvent,
    MaintenanceItem,
    MaintenanceTask,
    MaintenanceTool,
    WorkOrder,
    WorkOrderLotoCompletion,
    WorkOrderMaterialUsage,
    WorkOrderTaskCompletion,
    WorkOrderTool,
)
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db

User = get_user_model()

COMMAND = "report_duplicate_work_orders"
COMMAND_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "management" / "commands" / f"{COMMAND}.py"
)

BASE = timezone.make_aware(datetime(2026, 7, 1, 9, 0, 0))
DUE = date(2026, 7, 15)


def _item(title, asset_name):
    asset = AssetFactory(name=asset_name)
    return MaintenanceItem.objects.create(
        asset=asset,
        title=title,
        description="Routine preventive maintenance",
        interval_days=30,
    )


def _work_order(item, *, created_at, due_date=DUE, status=WorkOrder.Status.OPEN, **kwargs):
    """Create a work order and force its auto-now timestamps to ``created_at``."""
    wo = WorkOrder.objects.create(
        maintenance_item=item,
        asset=item.asset,
        due_date=due_date,
        status=status,
        **kwargs,
    )
    # ``created_at``/``updated_at`` are auto_now_add/auto_now, so a plain save()
    # cannot backdate them. This is test seeding, not the command under test.
    WorkOrder.objects.filter(pk=wo.pk).update(created_at=created_at, updated_at=created_at)
    wo.refresh_from_db()
    return wo


def _run(*args):
    out = StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def seeded():
    """Four groups: retry cluster, worked cluster, legitimate repeat, receipt-only."""
    operator = User.objects.create_user(
        username="scantty-op", email="op@example.com", password="op-password", is_staff=True
    )
    planner = User.objects.create_user(
        username="pm-planner", email="planner@example.com", password="pl-password", is_staff=True
    )

    # --- Group A: the BACKEND-18 retry cluster. Three commits seconds apart,
    # none audited, none touched by anyone since.
    item_a = _item("Spindle lubrication", "Haas TM-1 Mill")
    MaintenanceTask.objects.create(maintenance_item=item_a, order=1, title="Grease spindle")
    retry = [
        _work_order(item_a, created_at=BASE),
        _work_order(item_a, created_at=BASE + timedelta(seconds=11)),
        _work_order(item_a, created_at=BASE + timedelta(seconds=26)),
    ]
    for wo in retry:
        WorkOrderTaskCompletion.objects.create(
            work_order=wo,
            task=item_a.tasks.first(),
            task_title="Grease spindle",
            task_order=1,
        )
        WorkOrderLotoCompletion.objects.create(
            work_order=wo, source_type="electrical", source_label="Main breaker"
        )
    # Generation pre-fills the timestamps of the child rows too; keep the WOs
    # themselves looking untouched.
    WorkOrder.objects.filter(pk__in=[wo.pk for wo in retry]).update(updated_at=BASE)

    # --- Group B: same bug, but one of the duplicates has since been worked.
    item_b = _item("Filter change", "Laguna Dust Collector")
    MaintenanceTask.objects.create(maintenance_item=item_b, order=1, title="Swap filter")
    untouched = _work_order(item_b, created_at=BASE + timedelta(hours=1))
    worked = _work_order(
        item_b,
        created_at=BASE + timedelta(hours=1, seconds=9),
        status=WorkOrder.Status.COMPLETED,
        completed_by_name="A. Tech",
        elapsed_seconds=2700,
    )
    for wo in (untouched, worked):
        WorkOrderTaskCompletion.objects.create(
            work_order=wo,
            task=item_b.tasks.first(),
            task_title="Swap filter",
            task_order=1,
            is_completed=(wo == worked),
            completed_by=operator if wo == worked else None,
            completed_at=timezone.now() if wo == worked else None,
        )
    WorkOrder.objects.filter(pk=untouched.pk).update(updated_at=untouched.created_at)
    WorkOrder.objects.filter(pk=worked.pk).update(completed_at=BASE + timedelta(hours=5))

    # --- Group C: a legitimately repeated PM. Same item and due date, but the
    # two work orders were raised five days apart through the audited create
    # path — the false positive the report must rank low, not hide.
    item_c = _item("Quarterly inspection", "Epilog Fusion Pro 32")
    legit_first = _work_order(item_c, created_at=BASE - timedelta(days=10))
    legit_second = _work_order(item_c, created_at=BASE - timedelta(days=5))
    for wo, actor in ((legit_first, planner), (legit_second, operator)):
        event = MaintenanceAuditEvent.objects.create(
            action=MaintenanceAuditEvent.Action.WO_CREATE, actor=actor, work_order=wo
        )
        MaintenanceAuditEvent.objects.filter(pk=event.pk).update(created_at=wo.created_at)
        WorkOrder.objects.filter(pk=wo.pk).update(updated_at=wo.created_at)

    # --- Group E: the duplicate whose ONLY trace is an out-of-pocket material.
    # ``add_material`` writes the usage row with was_used=False,
    # applied_quantity=None and quantity_planned == quantity_used, and returns
    # without touching the work order at all — so status, updated_at and every
    # other WorkOrder-level signal still read "untouched" while a real receipt
    # and a real unit cost hang off the job.
    item_e = _item("Coolant top-up", "Tormach 1100MX")
    receipted = _work_order(item_e, created_at=BASE + timedelta(hours=2))
    bare = _work_order(item_e, created_at=BASE + timedelta(hours=2, seconds=7))
    WorkOrderMaterialUsage.objects.create(
        work_order=receipted,
        material=None,
        is_ad_hoc=True,
        material_name="Coolant concentrate",
        quantity_planned=Decimal("1.00"),
        quantity_used=Decimal("1.00"),
        unit="L",
        was_used=False,
        applied_quantity=None,
        unit_cost=Decimal("28.40"),
        receipt_image="work_orders/receipts/2026/07/coolant.jpg",
    )

    # --- A lone work order that must never appear: no duplicate partner.
    item_d = _item("Annual PAT test", "Bench Grinder")
    _work_order(item_d, created_at=BASE)

    return {
        "operator": operator,
        "planner": planner,
        "item_a": item_a,
        "item_b": item_b,
        "item_c": item_c,
        "item_d": item_d,
        "item_e": item_e,
        "retry": retry,
        "untouched": untouched,
        "worked": worked,
        "legit": [legit_first, legit_second],
        "receipted": receipted,
        "bare": bare,
    }


def _payload(**kwargs):
    out = StringIO()
    call_command(COMMAND, "--format", "json", stdout=out, **kwargs)
    return json.loads(out.getvalue())


def _groups(**kwargs):
    return {g["maintenance_item_id"]: g for g in _payload(**kwargs)["groups"]}


def _rows(group):
    return {row["id"]: row for row in group["work_orders"]}


# --------------------------------------------------------------- the report


def test_retry_cluster_is_reported_as_high_confidence(seeded):
    group = _groups()[str(seeded["item_a"].id)]

    assert group["count"] == 3
    assert group["confidence"] == "high"
    assert group["largest_burst"] == 3
    assert group["worked_count"] == 0
    assert group["unknown_count"] == 0
    assert group["no_recorded_work_count"] == 3
    assert "within" in group["confidence_reason"]

    # The earliest is named as the intended one, precisely because nothing has
    # been worked and the later rows only exist because of the retry.
    assert group["suggested_keep_id"] == str(seeded["retry"][0].id)
    assert "earliest" in group["suggested_keep_reason"]


def test_group_names_the_maintenance_item_asset_and_shared_due_date(seeded):
    group = _groups()[str(seeded["item_a"].id)]

    assert group["maintenance_item_title"] == "Spindle lubrication"
    assert "Haas TM-1 Mill" in group["asset"]
    assert group["due_date"] == DUE.isoformat()
    assert group["count"] == 3


def test_each_work_order_reports_id_number_status_and_creation(seeded):
    group = _groups()[str(seeded["item_a"].id)]
    first = group["work_orders"][0]

    assert first["id"] == str(seeded["retry"][0].id)
    # The number is the model's own short id, so it matches every other surface
    # (admin, PDF forms, child-row __str__) character for character.
    assert first["number"] == seeded["retry"][0].short_id
    assert first["number"] == f"WO-{str(seeded['retry'][0].id)[:8].upper()}"
    assert first["status"] == "open"
    assert first["created_at"].startswith("2026-07-01T09:00")
    # generate_work_order writes no audit row, so there is no creator to name.
    assert first["created_by"] == ""
    assert "not recorded" in first["created_by_source"]


def test_worked_work_order_is_distinguished_from_its_untouched_duplicate(seeded):
    group = _groups()[str(seeded["item_b"].id)]
    rows = _rows(group)

    worked = rows[str(seeded["worked"].id)]
    untouched = rows[str(seeded["untouched"].id)]

    assert worked["verdict"] == "worked"
    assert worked["worked"] is True
    assert worked["work_signals"]["tasks_completed"] == 1
    assert worked["work_signals"]["time_seconds"] == 2700
    assert worked["work_signals"]["completed_by"] == "A. Tech"
    assert worked["work_signals"]["status_beyond_open"] is True

    assert untouched["verdict"] == "no-recorded-work"
    assert untouched["worked"] is False
    assert untouched["cannot_tell_because"] == []
    assert untouched["work_signals"]["tasks_completed"] == 0
    assert untouched["work_signals"]["time_seconds"] == 0

    assert group["worked_count"] == 1
    assert group["suggested_keep_id"] == str(seeded["worked"].id)
    assert "only work order" in group["suggested_keep_reason"]


def test_prefilled_material_quantity_is_not_treated_as_work(seeded):
    """generate_work_order pre-fills quantity_used, so it cannot mean 'used'."""
    from inventory.models import MaintenanceMaterial

    item = seeded["item_a"]
    material = MaintenanceMaterial.objects.create(
        maintenance_item=item, name="Way oil", quantity=Decimal("2.00"), unit="L"
    )
    for wo in seeded["retry"]:
        WorkOrderMaterialUsage.objects.create(
            work_order=wo,
            material=material,
            material_name="Way oil",
            quantity_planned=Decimal("2.00"),
            quantity_used=Decimal("2.00"),  # pre-filled by generation, never touched
            unit="L",
        )
    WorkOrder.objects.filter(pk__in=[wo.pk for wo in seeded["retry"]]).update(updated_at=BASE)

    group = _groups()[str(item.id)]
    assert group["worked_count"] == 0
    assert group["no_recorded_work_count"] == 3
    for row in group["work_orders"]:
        assert row["work_signals"]["materials_total"] == 1
        assert row["work_signals"]["materials_used"] == 0
        assert row["work_signals"]["qty_edited"] == 0
        assert row["work_signals"]["materials_applied"] == 0
        assert row["work_signals"]["adhoc_materials"] == 0
        assert row["work_signals"]["material_evidence"] == 0


def test_legitimate_repeat_is_flagged_as_a_likely_false_positive(seeded):
    group = _groups()[str(seeded["item_c"].id)]

    assert group["count"] == 2
    assert group["confidence"] == "low"
    assert group["largest_burst"] == 1
    assert "no burst" in group["confidence_reason"]
    assert "legitimately repeated" in group["confidence_reason"]

    # ...and the "which one was intended?" line does not assume retry damage.
    assert "more likely two intentional work orders" in group["suggested_keep_reason"]

    # Creators are recoverable here because these went through the audited path.
    creators = {row["created_by"] for row in group["work_orders"]}
    assert creators == {"pm-planner", "scantty-op"}

    # A wo_create audit row is not itself "other activity" on the work order.
    for row in group["work_orders"]:
        assert row["work_signals"]["other_audit_events"] == 0
        assert row["verdict"] == "no-recorded-work"

    # Ranked low, but still present — ranking, never silent exclusion.
    assert str(seeded["item_c"].id) in _groups()


def test_high_confidence_groups_are_listed_before_low_confidence_ones(seeded):
    order = [g["confidence"] for g in _payload()["groups"]]
    assert order == sorted(order, key=lambda c: {"high": 0, "medium": 1, "low": 2}[c])


def test_work_orders_without_a_duplicate_partner_are_not_reported(seeded):
    assert str(seeded["item_d"].id) not in _groups()


def test_window_seconds_controls_burst_detection(seeded):
    # Widened to a fortnight, even the five-day-apart legitimate repeat clusters.
    group = _groups(window_seconds=14 * 86400)[str(seeded["item_c"].id)]
    assert group["largest_burst"] == 2
    # ...but it still is not called high confidence, because both rows carry a
    # WO_CREATE audit row and so came through the audited create path.
    assert group["confidence"] == "medium"


def test_in_burst_names_the_rows_the_confidence_ranking_counted(seeded):
    """``in_burst`` must agree with the sliding window ``largest_burst`` used.

    Three rows at t=0, t=310s and t=320s inside a 300s window: the burst is the
    310/320 pair, and the t=0 row is the one row *outside* it.
    """
    item = _item("Belt tension check", "Shopbot PRSalpha")
    early = _work_order(item, created_at=BASE)
    pair = [
        _work_order(item, created_at=BASE + timedelta(seconds=310)),
        _work_order(item, created_at=BASE + timedelta(seconds=320)),
    ]

    group = _groups()[str(item.id)]
    rows = _rows(group)

    assert group["largest_burst"] == 2
    assert rows[str(early.id)]["in_burst"] is False
    assert [rows[str(wo.id)]["in_burst"] for wo in pair] == [True, True]


# --------------------------------------------------- "has this been worked?"


def test_adhoc_material_with_a_receipt_is_never_called_untouched(seeded):
    """The receipt case: real money on the job, nothing moved on the WO row."""
    group = _groups()[str(seeded["item_e"].id)]
    rows = _rows(group)
    receipted = rows[str(seeded["receipted"].id)]
    bare = rows[str(seeded["bare"].id)]

    # Every WorkOrder-level signal still reads untouched...
    assert receipted["work_signals"]["status_beyond_open"] is False
    assert receipted["work_signals"]["edited_since_create"] is False
    assert receipted["work_signals"]["materials_used"] == 0
    assert receipted["work_signals"]["materials_applied"] == 0
    assert receipted["work_signals"]["qty_edited"] == 0
    # ...and the child row is still what makes this work order worked.
    assert receipted["work_signals"]["adhoc_materials"] == 1
    assert receipted["work_signals"]["material_evidence"] == 1
    assert receipted["verdict"] == "worked"

    assert bare["verdict"] == "no-recorded-work"
    assert group["worked_count"] == 1
    assert group["suggested_keep_id"] == str(seeded["receipted"].id)
    assert "nothing is lost either way" not in group["suggested_keep_reason"]
    assert "nothing recorded is lost either way" not in group["suggested_keep_reason"]


def test_task_notes_and_step_timers_count_as_work(seeded):
    """complete_task with is_completed=false still saves notes and step time."""
    item = _item("Gearbox oil check", "Bridgeport Mill")
    MaintenanceTask.objects.create(maintenance_item=item, order=1, title="Check level")
    annotated = _work_order(item, created_at=BASE)
    timed = _work_order(item, created_at=BASE + timedelta(seconds=8))
    WorkOrderTaskCompletion.objects.create(
        work_order=annotated,
        task=item.tasks.first(),
        task_title="Check level",
        task_order=1,
        is_completed=False,
        notes="Sight glass cloudy, ordered a replacement",
    )
    WorkOrderTaskCompletion.objects.create(
        work_order=timed,
        task=item.tasks.first(),
        task_title="Check level",
        task_order=1,
        is_completed=False,
        elapsed_seconds=180,
    )

    rows = _rows(_groups()[str(item.id)])
    assert rows[str(annotated.id)]["work_signals"]["task_notes"] == 1
    assert rows[str(annotated.id)]["verdict"] == "worked"
    assert rows[str(timed.id)]["work_signals"]["task_timers"] == 1
    assert rows[str(timed.id)]["verdict"] == "worked"


def test_a_copied_tool_location_is_not_evidence_but_a_restaged_one_is(seeded):
    """create_work_order_tools copies location_hint, so only divergence counts."""
    item = _item("Chuck inspection", "Colchester Lathe")
    template = MaintenanceTool.objects.create(
        maintenance_item=item,
        name="Chuck key",
        location_hint="Tool crib, drawer 3",
        notes="Return to the crib",
    )
    copied = _work_order(item, created_at=BASE)
    restaged = _work_order(item, created_at=BASE + timedelta(seconds=6))
    WorkOrderTool.objects.create(
        work_order=copied,
        tool=template,
        name="Chuck key",
        location_hint="Tool crib, drawer 3",
        notes="Return to the crib",
    )
    WorkOrderTool.objects.create(
        work_order=restaged,
        tool=template,
        name="Chuck key",
        location_hint="Bench 2",
        notes="Return to the crib",
    )

    rows = _rows(_groups()[str(item.id)])
    assert rows[str(copied.id)]["work_signals"]["tools_restaged"] == 0
    assert rows[str(copied.id)]["verdict"] == "no-recorded-work"
    assert rows[str(restaged.id)]["work_signals"]["tools_restaged"] == 1
    assert rows[str(restaged.id)]["verdict"] == "worked"


def test_an_assigned_duplicate_is_cannot_tell_not_untouched(seeded):
    """"We cannot tell" is a different answer from "we checked and found nothing"."""
    item = _item("Compressor drain", "Ingersoll Rand 2475")
    plain = _work_order(item, created_at=BASE)
    assigned = _work_order(
        item, created_at=BASE + timedelta(seconds=12), assigned_to=seeded["operator"]
    )

    group = _groups()[str(item.id)]
    rows = _rows(group)

    assigned_row = rows[str(assigned.id)]
    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert assigned_row["verdict"] == "unknown"
    assert assigned_row["worked"] is False
    assert any("assigned to" in reason for reason in assigned_row["cannot_tell_because"])

    assert group["worked_count"] == 0
    assert group["unknown_count"] == 1
    assert group["no_recorded_work_count"] == 1

    # The sentence that authorises a deletion must not be printed here.
    assert "nothing is lost either way" not in group["suggested_keep_reason"]
    assert "nothing recorded is lost either way" not in group["suggested_keep_reason"]
    assert "CANNOT BE SHOWN to be untouched" in group["suggested_keep_reason"]

    text = _run()
    assert "CANNOT TELL" in text


def test_a_tool_row_whose_template_is_gone_is_indeterminate(seeded):
    """Deleting the template makes 'was this restaged?' unanswerable, not 'no'."""
    item = _item("Blade change", "Powermatic Bandsaw")
    template = MaintenanceTool.objects.create(
        maintenance_item=item, name="Blade wrench", location_hint="Saw cabinet"
    )
    plain = _work_order(item, created_at=BASE)
    orphaned = _work_order(item, created_at=BASE + timedelta(seconds=15))
    WorkOrderTool.objects.create(
        work_order=orphaned, tool=template, name="Blade wrench", location_hint="Saw cabinet"
    )
    template.delete()  # SET_NULL: the work-order row survives without its spec

    rows = _rows(_groups()[str(item.id)])
    orphaned_row = rows[str(orphaned.id)]
    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert orphaned_row["verdict"] == "unknown"
    assert orphaned_row["work_signals"]["tools_unverifiable"] == 1
    assert any("template row" in reason for reason in orphaned_row["cannot_tell_because"])


def test_untouched_retry_rows_are_worded_as_no_recorded_work(seeded):
    """"No evidence of work" overclaims; the report only checked its own signals."""
    text = _run()
    assert "no recorded work in the signals checked" in text
    assert "no evidence of work" not in text
    assert "NOT COVERED AT ALL" in text
    assert "never scanned back" in text


# ------------------------------------------------------------ scope reporting


def test_since_selects_whole_groups_including_members_before_the_cutoff(seeded):
    """A burst straddling the cutoff must keep its count and its earliest row."""
    item = _item("Rail wipe-down", "Tormach 770M")
    before = _work_order(item, created_at=timezone.make_aware(datetime(2026, 6, 30, 23, 59, 55)))
    after = [
        _work_order(item, created_at=timezone.make_aware(datetime(2026, 7, 1, 0, 0, 3))),
        _work_order(item, created_at=timezone.make_aware(datetime(2026, 7, 1, 0, 0, 12))),
    ]

    group = _groups(since="2026-07-01")[str(item.id)]

    assert group["count"] == 3
    assert group["largest_burst"] == 3
    assert set(_rows(group)) == {str(before.id)} | {str(wo.id) for wo in after}
    # The pre-cutoff row is the earliest, so it is the one the operator asked for.
    assert group["suggested_keep_id"] == str(before.id)


def test_since_still_excludes_groups_with_no_member_after_the_cutoff(seeded):
    groups = _groups(since="2026-07-01")
    assert str(seeded["item_a"].id) in groups
    assert str(seeded["item_c"].id) not in groups


def test_limit_reports_the_full_total_alongside_the_groups_it_shows(seeded):
    payload = _payload(limit=1)

    assert payload["group_count"] == 1
    assert len(payload["groups"]) == 1
    assert payload["total_group_count"] == 4
    assert payload["matching_group_count"] == 4
    assert payload["groups_not_shown_due_to_limit"] == 3
    assert payload["confidence_tally_all"] == {"high": 3, "medium": 0, "low": 1}

    text = _run("--limit", "1")
    assert "Showing 1 of 4 suspected group(s) found." in text
    assert "3 further group(s) not shown because of --limit 1." in text


def test_min_confidence_filters_out_the_likely_false_positives_and_says_so(seeded):
    payload = _payload(min_confidence="high")
    shown = {g["maintenance_item_id"] for g in payload["groups"]}

    assert str(seeded["item_c"].id) not in shown
    assert str(seeded["item_a"].id) in shown

    assert payload["total_group_count"] == 4
    assert payload["matching_group_count"] == 3
    assert payload["groups_hidden_by_min_confidence"] == 1
    assert payload["groups_hidden_by_min_confidence_tally"]["low"] == 1

    text = _run("--min-confidence", "high")
    assert "Showing 3 of 4 suspected group(s) found." in text
    assert "1 group(s) hidden by --min-confidence high" in text


# ------------------------------------------------------- wording and formats


def test_text_output_says_suspected_and_names_false_positive_causes(seeded):
    out = _run()

    assert "SUSPECTED duplicate maintenance work orders" in out
    assert "not proven" in out
    assert "re-scheduled" in out
    assert "READ-ONLY REPORT" in out
    assert "Spindle lubrication" in out
    assert "Haas TM-1 Mill" in out
    assert "LIKELY INTENDED" in out
    assert "WORKED?" in out


def test_text_output_with_no_duplicates_still_explains_itself():
    out = _run()
    assert "No (maintenance item, due date) pair has more than one work order." in out
    assert "SUSPECTED" in out


def test_json_output_carries_the_suspected_caveat(seeded):
    payload = json.loads(_run("--format", "json"))

    assert payload["read_only"] is True
    assert payload["suspected_not_confirmed"] is True
    assert "re-scheduled" in payload["false_positive_causes"]
    assert "quantity_used alone is NOT evidence" in payload["worked_signal_definitions"]
    assert "NOT COVERED AT ALL" in payload["coverage_caveat"]
    assert "does NOT mean" in payload["coverage_caveat"]
    assert payload["group_count"] == 4
    assert payload["total_group_count"] == 4


def test_csv_output_is_one_row_per_work_order_and_carries_the_caveat(seeded):
    import csv as csv_module

    text = _run("--format", "csv")
    assert "SUSPECTED duplicates only" in text.splitlines()[0]

    rows = [r for r in csv_module.reader(StringIO(text)) if r]
    preamble = [r[0] for r in rows if r[0].startswith("#")]
    assert any("Showing 4 of 4 suspected group(s) found." in line for line in preamble)
    assert any("NOT COVERED AT ALL" in line for line in preamble)

    header_index = next(i for i, r in enumerate(rows) if "work_order_id" in r)
    header = rows[header_index]
    body = rows[header_index + 1 :]
    assert "verdict" in header
    assert "cannot_tell_because" in header
    assert len(body) == 9  # 3 + 2 + 2 + 2 work orders


def test_bad_since_is_rejected():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="--since must be YYYY-MM-DD"):
        _run("--since", "last tuesday")


# ------------------------------------------------------------- read-only guard


def test_help_text_records_that_the_command_is_read_only():
    from inventory.management.commands.report_duplicate_work_orders import Command

    help_text = Command.help
    assert "READ-ONLY" in help_text
    assert "no cleanup" in help_text or "cleanup" in help_text
    assert "--fix" in help_text


#: Mutating ORM methods that are never anything else, whatever the receiver.
ALWAYS_FORBIDDEN = {
    "save",
    "delete",
    "get_or_create",
    "update_or_create",
    "bulk_create",
    "bulk_update",
    "raw",
    "execute",
}

#: Mutating on a manager, queryset or related manager, but perfectly ordinary
#: on a plain list/dict/set — so these are only offences when the receiver is
#: not a local container.
FORBIDDEN_UNLESS_PLAIN_CONTAINER = {"update", "create", "add", "remove", "set", "clear"}

BUILTIN_CONTAINERS = {"set", "dict", "list", "frozenset"}


def _container_locals(tree):
    """Names in ``tree`` bound *only ever* to a plain list/dict/set.

    A name that is anywhere else assigned something other than a container is
    excluded, so a queryset cannot be smuggled past the guard by reusing a name
    that happens to hold a list somewhere else in the module.
    """
    containers = set()
    others = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        literal = isinstance(
            value, (ast.Dict, ast.Set, ast.List, ast.DictComp, ast.SetComp, ast.ListComp)
        )
        builtin = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in BUILTIN_CONTAINERS
        )
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if literal or builtin:
            containers |= names
        else:
            others |= names
    return containers - others


def _is_plain_container(receiver, container_locals):
    if isinstance(receiver, (ast.Dict, ast.Set, ast.List)):
        return True
    if isinstance(receiver, ast.Name):
        return receiver.id in container_locals
    return (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id in BUILTIN_CONTAINERS
    )


def test_command_source_contains_no_mutating_orm_calls():
    """Standing tripwire: nobody wires cleanup into this command later.

    DELIBERATE, USER-APPROVED EXCEPTION to the project rule against tests whose
    only evidence is the implementation's own source. The rule is right in
    general — source inspection is normally a poor substitute for behaviour —
    but the guarantee this command has to carry is "it can never write to the
    captain's production database". A behavioural test only proves the paths it
    exercises; this tripwire covers the path nobody thought to test, and it is
    paired with the behavioural ``test_running_the_report_changes_nothing``
    below, so it is belt AND braces rather than braces instead of belt. Please
    do not delete it as a rule violation.

    Walks the AST rather than grepping, so the help text and docstrings are
    free to *name* the forbidden calls while the code may not make them.
    """
    tree = ast.parse(COMMAND_SOURCE.read_text())
    container_locals = _container_locals(tree)

    offenders = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        if attr in ALWAYS_FORBIDDEN:
            offenders |= {ast.unparse(node.func)}
        elif attr in FORBIDDEN_UNLESS_PLAIN_CONTAINER and not _is_plain_container(
            node.func.value, container_locals
        ):
            offenders |= {ast.unparse(node.func)}

    assert not offenders, f"mutating call(s) {sorted(offenders)} must never appear in {COMMAND}"


def test_the_tripwire_would_catch_a_write_wired_into_the_command(tmp_path):
    """The guard above is only worth keeping if it actually fires."""
    source = tmp_path / "with_a_write.py"
    source.write_text(
        "def handle(self, qs, seen):\n"
        "    seen = set()\n"
        "    seen.add(1)\n"  # ordinary Python — must not be an offence
        "    qs.update(status='closed')\n"  # the write the tripwire exists for
    )
    tree = ast.parse(source.read_text())
    container_locals = _container_locals(tree)

    offences = sorted(
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (
            node.func.attr in ALWAYS_FORBIDDEN
            or (
                node.func.attr in FORBIDDEN_UNLESS_PLAIN_CONTAINER
                and not _is_plain_container(node.func.value, container_locals)
            )
        )
    )
    assert offences == ["qs.update"]


def test_running_the_report_changes_nothing(seeded):
    def snapshot():
        return sorted(
            (
                str(wo.id),
                wo.status,
                wo.due_date.isoformat(),
                wo.created_at.isoformat(),
                wo.updated_at.isoformat(),
                wo.elapsed_seconds,
                wo.completed_at.isoformat() if wo.completed_at else None,
            )
            for wo in WorkOrder.objects.all()
        )

    before = snapshot()
    counts_before = (
        WorkOrder.objects.count(),
        WorkOrderTaskCompletion.objects.count(),
        WorkOrderLotoCompletion.objects.count(),
        WorkOrderMaterialUsage.objects.count(),
        WorkOrderTool.objects.count(),
        MaintenanceAuditEvent.objects.count(),
        MaintenanceItem.objects.count(),
    )

    _run()
    _run("--format", "json")
    _run("--format", "csv")

    assert snapshot() == before
    assert (
        WorkOrder.objects.count(),
        WorkOrderTaskCompletion.objects.count(),
        WorkOrderLotoCompletion.objects.count(),
        WorkOrderMaterialUsage.objects.count(),
        WorkOrderTool.objects.count(),
        MaintenanceAuditEvent.objects.count(),
        MaintenanceItem.objects.count(),
    ) == counts_before
