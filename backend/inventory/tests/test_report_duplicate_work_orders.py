"""Tests for the read-only ``report_duplicate_work_orders`` management command.

The seeded data deliberately contains all five shapes the captain needs the
report to tell apart:

* a real BACKEND-18 retry cluster (three work orders seconds apart, untouched);
* a cluster where one of the duplicates has since been worked;
* a legitimately repeated PM raised twice, days apart, through the audited
  create path — which shares the (maintenance_item, due_date) grouping key and
  must therefore be flagged as a likely false positive rather than hidden;
* a cluster where the only trace of work on one duplicate is an ad-hoc material
  row carrying a receipt — nothing on the work order row itself moved, which is
  exactly the case a WorkOrder-only signal set calls "untouched";
* a cluster carrying NO due date at all, which is what the dashboard's Generate
  button leaves behind on a PM that has never been completed — the shape an
  earlier cut of this report filtered out before announcing "nothing to review".
"""

import ast
import itertools
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
    LocationProblem,
    MaintenanceAuditEvent,
    MaintenanceItem,
    MaintenanceTask,
    MaintenanceTool,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderLotoCompletion,
    WorkOrderMaterialUsage,
    WorkOrderOmrTemplate,
    WorkOrderPhoto,
    WorkOrderTaskCompletion,
    WorkOrderTool,
)
from inventory.tests.factories import AssetFactory, LocationFactory, SupplierFactory

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
    """Five groups: retry, worked, legitimate repeat, receipt-only, no-due-date."""
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
    # WorkOrderViewSet.perform_update writes this on every transition into
    # COMPLETED, so a work order finished through the API always carries one.
    MaintenanceAuditEvent.objects.create(
        action=MaintenanceAuditEvent.Action.WO_COMPLETE, actor=operator, work_order=worked
    )

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

    # --- Group F: the dashboard Generate button on a never-completed PM.
    # MaintenanceItem.next_due_at is None while last_completed_at is null, so
    # generate_work_order falls back to None and every retry lands with
    # due_date NULL. Grouping on (item, NULL) is well defined; skipping those
    # rows is how a report ends up announcing "nothing to review" over real
    # damage.
    item_f = _item("First-ever calibration", "Mitutoyo Height Gauge")
    undated = [
        _work_order(item_f, created_at=BASE + timedelta(hours=3), due_date=None),
        _work_order(item_f, created_at=BASE + timedelta(hours=3, seconds=13), due_date=None),
    ]

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
        "item_f": item_f,
        "retry": retry,
        "untouched": untouched,
        "worked": worked,
        "legit": [legit_first, legit_second],
        "receipted": receipted,
        "bare": bare,
        "undated": undated,
    }


def _payload(**kwargs):
    out = StringIO()
    call_command(COMMAND, "--format", "json", stdout=out, **kwargs)
    return json.loads(out.getvalue())


def _groups(**kwargs):
    return {g["maintenance_item_id"]: g for g in _payload(**kwargs)["groups"]}


def _rows(group):
    return {row["id"]: row for row in group["work_orders"]}


def _verdict_line_for(text, work_order):
    """The one "WORKED?" line the text report prints for ``work_order``.

    The report opens with a long preamble — the false-positive, signals and
    coverage notes — which necessarily *names* every verdict wording it can
    emit. An assertion over the whole document is therefore satisfied by that
    boilerplate rather than by the row under test, so per-row claims must be
    scoped to the row's own block.
    """
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if str(work_order.id) in line)
    return next(line for line in lines[start:] if line.strip().startswith("WORKED?"))


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
    assert "no wo_create audit row" in first["created_by_source"]
    assert "problem promotion write none" in first["created_by_source"]
    assert "migration 0057" in first["created_by_source"]


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

    # Completing it wrote a wo_complete audit row. That is the strongest
    # confirmation of work there is, so it must not also be reported as a
    # reason the report cannot tell — a filter on cannot_tell_because is how
    # the captain finds rows needing a hand check.
    assert worked["work_signals"]["other_audit_events"] == 1
    assert worked["cannot_tell_because"] == []
    assert "CANNOT TELL" not in _verdict_line_for(_run(), seeded["worked"])


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
    assert "more likely 2 intentional work orders" in group["suggested_keep_reason"]

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


def test_in_burst_marks_every_clustered_row_not_just_the_winning_run(seeded):
    """A group can hold two separate retry bursts; both are retry damage.

    Rows at t=0, +10s, +1h and +1h10s inside a 300s window are two pairs. Naming
    only the first pair would emit ``in_burst: false`` on two rows created ten
    seconds apart, and a captain filtering on it would lose half the damage.
    """
    item = _item("Chuck jaws swap", "Haas ST-20")
    offsets = (0, 10, 3600, 3610)
    burst_rows = [_work_order(item, created_at=BASE + timedelta(seconds=o)) for o in offsets]

    group = _groups()[str(item.id)]
    rows = _rows(group)

    assert group["largest_burst"] == 2
    assert [rows[str(wo.id)]["in_burst"] for wo in burst_rows] == [True, True, True, True]


def test_an_audited_row_outside_the_burst_does_not_rank_the_burst(seeded):
    """The ranked run is the population the ranking sentence names.

    Two dashboard generations 11s apart leave no wo_create row; a third work
    order raised by hand four hours later, under the same item and due date,
    does. That later row says nothing about how the pair was raised, so counting
    it would both mis-state the number the sentence quotes and demote a genuine
    retry pair to medium — which --min-confidence high would then hide.
    """
    item = _item("Way oil top-up", "Bridgeport Series I")
    retries = [
        _work_order(item, created_at=BASE),
        _work_order(item, created_at=BASE + timedelta(seconds=11)),
    ]
    by_hand = _work_order(item, created_at=BASE + timedelta(hours=4))
    event = MaintenanceAuditEvent.objects.create(
        action=MaintenanceAuditEvent.Action.WO_CREATE,
        actor=seeded["operator"],
        work_order=by_hand,
    )
    MaintenanceAuditEvent.objects.filter(pk=event.pk).update(created_at=by_hand.created_at)

    group = _groups()[str(item.id)]
    rows = _rows(group)
    reason = group["confidence_reason"]

    assert group["largest_burst"] == 2
    assert [rows[str(wo.id)]["in_burst"] for wo in retries] == [True, True]
    assert rows[str(by_hand.id)]["in_burst"] is False

    assert group["confidence"] == "high"
    assert "none of those 2 has a WO_CREATE audit row" in reason
    # The audited row is reported, but as a row outside every run, not folded in.
    assert "A further 1 work order(s) under this key carry a wo_create audit row" in reason
    assert "sit in no run at all" in reason
    assert rows[str(by_hand.id)]["created_by"] == "scantty-op"
    assert [rows[str(wo.id)]["created_by"] for wo in retries] == ["", ""]

    # Ranked high, so the flag the captain reaches for still shows the pair.
    assert str(item.id) in _groups(min_confidence="high")


def test_a_second_audited_run_does_not_rank_the_unaudited_one(seeded):
    """A group can hold two runs, and they are two populations, not one.

    Two dashboard generations 11s apart write no wo_create row; four hours later
    two work orders are raised by hand 20s apart under the same item and due
    date, and those DO write one. Both pairs are clusters, so counting audit rows
    across the union would quote a figure true of neither pair and demote a
    genuine retry burst to medium, which --min-confidence high then hides.
    """
    item = _item("Spindle belt check", "Hardinge HLV-H")
    retries = [
        _work_order(item, created_at=BASE),
        _work_order(item, created_at=BASE + timedelta(seconds=11)),
    ]
    by_hand = [
        _work_order(item, created_at=BASE + timedelta(hours=4)),
        _work_order(item, created_at=BASE + timedelta(hours=4, seconds=20)),
    ]
    for wo, actor in zip(by_hand, (seeded["operator"], seeded["planner"])):
        event = MaintenanceAuditEvent.objects.create(
            action=MaintenanceAuditEvent.Action.WO_CREATE, actor=actor, work_order=wo
        )
        MaintenanceAuditEvent.objects.filter(pk=event.pk).update(created_at=wo.created_at)

    group = _groups()[str(item.id)]
    rows = _rows(group)
    reason = group["confidence_reason"]

    # Every row is clustered — with something. That is not one population.
    assert [rows[str(wo.id)]["in_burst"] for wo in retries + by_hand] == [True] * 4
    assert group["largest_burst"] == 2

    assert group["confidence"] == "high"
    assert "none of those 2 has a WO_CREATE audit row" in reason
    assert "2 of the 4" not in reason
    # ...and the audited pair is named as the separate cluster it is.
    assert "1 other run(s) of work orders created within 300s of each other" in reason
    assert "hold 2 row(s) with a wo_create audit row between them" in reason
    assert "sit in no run at all" not in reason

    assert [rows[str(wo.id)]["created_by"] for wo in retries] == ["", ""]
    assert [rows[str(wo.id)]["created_by"] for wo in by_hand] == ["scantty-op", "pm-planner"]

    assert str(item.id) in _groups(min_confidence="high")


def test_the_low_confidence_keep_reason_counts_the_rows_it_describes(seeded):
    """That branch fires at any group size, so it must not assume a pair.

    A never-completed PM leaves due_date NULL, so three unhurried generations
    days apart share one key with no burst between any two of them.
    """
    item = _item("As-needed gutter clearing", "North Shed Roof")
    days = (0, 3, 9)
    spread = [_work_order(item, created_at=BASE + timedelta(days=d), due_date=None) for d in days]

    group = _groups()[str(item.id)]

    assert group["count"] == 3
    assert group["largest_burst"] == 1
    assert group["confidence"] == "low"
    assert group["suggested_keep_id"] == str(spread[0].id)
    assert "more likely 3 intentional work orders" in group["suggested_keep_reason"]
    assert "treating any of them as spurious" in group["suggested_keep_reason"]


# ------------------------------------------------------ null-key BACKEND-18 damage


def test_duplicates_with_no_due_date_are_found_and_reported(seeded):
    """The dashboard Generate button leaves due_date NULL on a never-run PM."""
    group = _groups()[str(seeded["item_f"].id)]

    assert group["count"] == 2
    assert group["due_date"] is None
    assert group["due_date_missing"] is True
    assert group["confidence"] == "high"
    assert set(_rows(group)) == {str(wo.id) for wo in seeded["undated"]}

    text = _run()
    assert "Due date     : (none set)" in text
    assert "no due date was recorded on any of these" in text


def test_work_orders_that_cannot_be_grouped_are_counted_not_dropped():
    """A corrective WO has no maintenance item, so this key cannot group it.

    It must be named as unsearched rather than quietly vanishing into a report
    that then announces there is nothing to review.
    """
    WorkOrder.objects.create(asset=AssetFactory(name="Shop Compressor"), due_date=DUE)

    payload = _payload()
    assert payload["searched"]["work_orders_total"] == 1
    assert payload["searched"]["groupable_total"] == 0
    assert payload["searched"]["ungroupable_no_item"] == 1
    assert payload["total_group_count"] == 0

    out = _run()
    assert "NOT SEARCHED: 1 work order(s) carry no maintenance item" in out
    assert "1 work order(s) with no maintenance item were never searched" in out


def test_the_run_accounts_for_every_work_order_it_looked_at(seeded):
    searched = _payload()["searched"]

    assert searched["work_orders_total"] == WorkOrder.objects.count()
    assert (
        searched["groupable_total"] + searched["ungroupable_no_item"]
        == searched["work_orders_total"]
    )
    assert (
        searched["rows_in_duplicate_groups"] + searched["lone_rows"] == searched["groupable_total"]
    )
    # 3 + 2 + 2 + 2 + 2 duplicate rows, and the lone Annual PAT test work order.
    assert searched["rows_in_duplicate_groups"] == 11
    assert searched["lone_rows"] == 1
    assert searched["duplicate_groups_found"] == 5


def test_since_counts_the_groups_it_excluded(seeded):
    payload = _payload(since="2026-07-01")

    assert payload["searched"]["groups_excluded_by_since"] == 1
    assert payload["searched"]["rows_excluded_by_since"] == 2

    text = _run("--since", "2026-07-01")
    assert "NOT SHOWN: --since 2026-07-01 excluded 1 duplicate group(s)" in text
    assert "re-run without --since to see them" in text


def test_the_headline_total_counts_groups_found_not_groups_selected(seeded):
    """A key named total_group_count may never be smaller than what was found."""
    payload = _payload(since="2026-07-01")

    assert payload["searched"]["duplicate_groups_found"] == 5
    assert payload["total_group_count"] == 5  # found, NOT the 4 that --since selected
    assert payload["selected_group_count"] == 4
    assert payload["groups_excluded_by_since"] == 1
    assert payload["group_count"] == 4

    text = _run("--since", "2026-07-01")
    assert "Showing 4 of 4 selected; 5 found, 1 excluded by --since 2026-07-01." in text
    # The breakdown is over the selected groups, and says so — with the
    # found-total on the same line, so neither number can be read as the other.
    assert "Confidence of the 4 selected (of 5 found): 4 high, 0 medium, 0 low." in text
    # The pre---since total must never be reported as the whole picture.
    assert "Showing 4 of 4 suspected group(s) found." not in text


def test_every_confidence_tally_sums_to_the_population_it_labels(seeded):
    """The invariant that makes the tally line readable at all.

    Each breakdown counts a named population, so it must add up to that
    population's size. An expectation that violates this cannot hold for any
    input, whichever side of it is wrong.
    """
    for payload in (_payload(), _payload(since="2026-07-01"), _payload(limit=1)):
        selected = payload["confidence_tally_selected"]
        shown = payload["confidence_tally_shown"]
        assert sum(selected.values()) == payload["selected_group_count"]
        assert sum(shown.values()) == payload["group_count"]
        # ...and the selected total can never exceed what the run found.
        assert payload["selected_group_count"] <= payload["total_group_count"]

    unfiltered = _payload()
    assert sum(unfiltered["confidence_tally_selected"].values()) == (
        unfiltered["total_group_count"]
    )


def _only_a_pre_cutoff_group():
    """One duplicate group, both members created before the 2026-07-01 cutoff."""
    item = _item("Quarterly inspection", "Epilog Fusion Pro 32")
    return item, [
        _work_order(item, created_at=BASE - timedelta(days=10)),
        _work_order(item, created_at=BASE - timedelta(days=5)),
    ]


def test_since_excluding_every_group_never_claims_there_are_no_duplicates():
    """The run found a duplicate; --since hid it. That is not an absence.

    Regression for the headline reading "NO DUPLICATE GROUP FOUND" — and the
    established-absence sentence under it — over a population the same run had
    just proved contains a shared key.
    """
    item, work_orders = _only_a_pre_cutoff_group()

    text = _run("--since", "2026-07-01")
    assert "NO GROUP SURVIVED --since 2026-07-01" in text
    assert "This run FOUND 1 duplicate group(s) (2 work order(s)) and excluded every one" in text
    assert "Re-run without --since to see them." in text
    assert "NO DUPLICATE GROUP FOUND" not in text
    assert "no (maintenance item, due date) key is shared by more than one" not in text

    payload = _payload(since="2026-07-01")
    assert payload["no_groups_reason"] == "all-excluded-by-since"
    assert payload["total_group_count"] == 1
    assert payload["selected_group_count"] == 0
    assert payload["group_count"] == 0
    assert "NO GROUP SURVIVED --since 2026-07-01" in payload["nothing_found_note"]
    assert "NO DUPLICATE GROUP FOUND" not in payload["nothing_found_note"]

    csv_text = _run("--format", "csv", "--since", "2026-07-01")
    assert "NO GROUP SURVIVED --since 2026-07-01" in csv_text
    assert "NO DUPLICATE GROUP FOUND" not in csv_text

    # ...and dropping the flag finds exactly the group it said it had found.
    assert str(item.id) in _groups()
    assert _groups()[str(item.id)]["count"] == len(work_orders)


def test_min_confidence_hiding_every_group_never_claims_there_are_no_duplicates():
    """Same principle one filter later: hidden is not absent."""
    _only_a_pre_cutoff_group()  # two WOs five days apart -> low confidence

    text = _run("--min-confidence", "high")
    assert "NO GROUP SURVIVED THE OUTPUT FILTERS" in text
    assert "This run FOUND 1 duplicate group(s) and selected 1" in text
    assert "--min-confidence high hid 1" in text
    assert "NO DUPLICATE GROUP FOUND" not in text

    payload = _payload(min_confidence="high")
    assert payload["no_groups_reason"] == "all-filtered-out"
    assert payload["total_group_count"] == 1
    assert payload["group_count"] == 0


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


def test_a_tool_staging_divergence_is_cannot_tell_not_work(seeded):
    """A hint that matches the template proves nothing; one that differs proves
    nothing either, because MaintenanceTool records no modification time."""
    item = _item("Chuck inspection", "Colchester Lathe")
    template = MaintenanceTool.objects.create(
        maintenance_item=item,
        name="Chuck key",
        location_hint="Tool crib, drawer 3",
        notes="Return to the crib",
    )
    copied = _work_order(item, created_at=BASE)
    divergent = _work_order(item, created_at=BASE + timedelta(seconds=6))
    WorkOrderTool.objects.create(
        work_order=copied,
        tool=template,
        name="Chuck key",
        location_hint="Tool crib, drawer 3",
        notes="Return to the crib",
    )
    WorkOrderTool.objects.create(
        work_order=divergent,
        tool=template,
        name="Chuck key",
        location_hint="Bench 2",
        notes="Return to the crib",
    )

    rows = _rows(_groups()[str(item.id)])
    divergent_row = rows[str(divergent.id)]
    assert rows[str(copied.id)]["work_signals"]["tools_restaged"] == 0
    assert rows[str(copied.id)]["verdict"] == "no-recorded-work"
    assert divergent_row["work_signals"]["tools_restaged"] == 1
    assert divergent_row["verdict"] == "unknown"
    assert any("cannot be told apart" in r for r in divergent_row["cannot_tell_because"])


def test_editing_a_tool_template_does_not_mark_untouched_retries_as_worked(seeded):
    """The template is freely editable and never re-syncs onto work orders.

    Reorganising the tool crib after a retry burst must not turn three rows
    nobody ever opened into "AMBIGUOUS — 3 of these have been worked".
    """
    item = _item("Tailstock service", "Hardinge HLV")
    template = MaintenanceTool.objects.create(
        maintenance_item=item, name="Drift", location_hint="Crib 3"
    )
    retries = [
        _work_order(item, created_at=BASE + timedelta(seconds=offset)) for offset in (0, 7, 19)
    ]
    for wo in retries:
        WorkOrderTool.objects.create(
            work_order=wo, tool=template, name="Drift", location_hint="Crib 3"
        )
    # The crib is reorganised long after the retries; nobody touches the WOs.
    MaintenanceTool.objects.filter(pk=template.pk).update(location_hint="Crib 5")

    group = _groups()[str(item.id)]
    assert group["worked_count"] == 0
    assert group["unknown_count"] == 3
    assert "AMBIGUOUS" not in group["suggested_keep_reason"]
    # ...and it is still not called untouched, because it genuinely cannot tell.
    assert group["no_recorded_work_count"] == 0
    assert "CANNOT BE SHOWN to be untouched" in group["suggested_keep_reason"]


def test_an_unreceived_purchase_order_line_is_not_treated_as_untouched(seeded):
    """Parts ordered for a job leave no other trace until the line is received.

    ``post_work_order_material`` only mirrors a PO line onto the work order at
    receiving time, from ``quantity_received``. Until then nothing moves on the
    work order — and ``PurchaseOrderItem.work_order`` is SET_NULL, so deleting
    the work order silently detaches the order instead of failing.
    """
    from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

    item = _item("Pump seal replacement", "Grundfos Circulator")
    plain = _work_order(item, created_at=BASE)
    ordered_for = _work_order(item, created_at=BASE + timedelta(seconds=14))

    order = PurchaseOrder.objects.create(supplier=SupplierFactory(), created_by=seeded["operator"])
    PurchaseOrderItem.objects.create(
        purchase_order=order,
        description="Seal kit",
        quantity_ordered=1,
        quantity_received=0,
        unit_cost_ordered=Decimal("42.00"),
        work_order=ordered_for,
    )

    group = _groups()[str(item.id)]
    rows = _rows(group)
    ordered_row = rows[str(ordered_for.id)]

    # Nothing at all moved on the work order itself...
    assert ordered_row["work_signals"]["status_beyond_open"] is False
    assert ordered_row["work_signals"]["edited_since_create"] is False
    assert ordered_row["work_signals"]["materials_total"] == 0
    # ...but the order is tied to this row and must not be called untouched.
    assert ordered_row["work_signals"]["purchase_order_items"] == 1
    assert ordered_row["verdict"] == "worked"
    assert ordered_row["worked_because"] == ["purchase_order_items"]

    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert group["suggested_keep_id"] == str(ordered_for.id)
    assert "nothing recorded is lost either way" not in group["suggested_keep_reason"]
    assert "nothing is lost either way" not in group["suggested_keep_reason"]

    # The captain is told this is an attachment, not somebody working the job.
    verdict_line = _verdict_line_for(_run(), ordered_for)
    assert "YES — as ATTACHED RECORDS, not as recorded work: purchase_order_items" in (verdict_line)
    assert "Deleting this work order would silently detach them" in verdict_line
    # Nothing indeterminate here, so THIS row's verdict claims nothing further.
    # Scoped to the row: the preamble names every verdict wording, so a
    # document-wide check would pass on boilerplate whatever the row said.
    assert ordered_row["cannot_tell_because"] == []
    assert "CANNOT TELL" not in verdict_line


def test_a_worked_row_still_reports_what_it_could_not_tell(seeded):
    """A positive finding must not swallow an unknown computed on the same row.

    A tech orders a seal kit against WO#2 and prints its OMR paper form. The
    order is a positive finding; the printed form is an explicit unknown — a
    paper copy may have been worked and never scanned back. Reporting only the
    order would state an absence over a case the report itself computed as
    indeterminate.
    """
    from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

    item = _item("Gearbox reseal", "Sumitomo Gearbox")
    plain = _work_order(item, created_at=BASE)
    both = _work_order(item, created_at=BASE + timedelta(seconds=13))

    order = PurchaseOrder.objects.create(supplier=SupplierFactory(), created_by=seeded["operator"])
    PurchaseOrderItem.objects.create(
        purchase_order=order,
        description="Seal kit",
        quantity_ordered=1,
        quantity_received=0,
        unit_cost_ordered=Decimal("42.00"),
        work_order=both,
    )
    WorkOrderOmrTemplate.objects.create(
        work_order=both, template_version=1, page_w_pt=595.0, page_h_pt=842.0
    )

    group = _groups()[str(item.id)]
    rows = _rows(group)
    both_row = rows[str(both.id)]

    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert both_row["verdict"] == "worked"
    assert both_row["worked_because"] == ["purchase_order_items"]
    # The unknown is not swallowed: it reaches JSON...
    assert any("never scanned back" in r for r in both_row["cannot_tell_because"])

    # ...this row's own verdict line...
    text = _run()
    verdict_line = _verdict_line_for(text, both)
    assert "AND CANNOT TELL:" in verdict_line
    assert "may have been worked and never scanned back" in verdict_line
    # The untouched sibling makes no such claim, so the wording is the row's.
    assert "CANNOT TELL" not in _verdict_line_for(text, plain)
    assert "Nobody is shown to have worked this job" not in text

    # ...and CSV, where an empty cell would tell a consumer there was no doubt.
    import csv as csv_module

    csv_rows = [r for r in csv_module.reader(StringIO(_run("--format", "csv"))) if r]
    header_index = next(i for i, r in enumerate(csv_rows) if "work_order_id" in r)
    header = csv_rows[header_index]
    row = next(
        r for r in csv_rows[header_index + 1 :] if r[header.index("work_order_id")] == str(both.id)
    )
    assert "never scanned back" in row[header.index("cannot_tell_because")]


def test_an_order_level_purchase_order_is_also_an_attachment(seeded):
    """PurchaseOrder.work_order is the same association one level up."""
    from reorder_queue.models import PurchaseOrder

    item = _item("Belt replacement", "Baldor Motor")
    plain = _work_order(item, created_at=BASE)
    ordered_for = _work_order(item, created_at=BASE + timedelta(seconds=9))
    PurchaseOrder.objects.create(
        supplier=SupplierFactory(), created_by=seeded["operator"], work_order=ordered_for
    )

    rows = _rows(_groups()[str(item.id)])
    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert rows[str(ordered_for.id)]["work_signals"]["purchase_orders"] == 1
    assert rows[str(ordered_for.id)]["verdict"] == "worked"


def test_a_promoted_problem_report_is_an_attachment_not_an_absence(seeded):
    """LocationProblem.work_order is SET_NULL: deleting the WO detaches it."""
    item = _item("As-needed building repair", "Main Workshop Fabric")
    plain = _work_order(item, created_at=BASE)
    promoted = _work_order(item, created_at=BASE + timedelta(seconds=11))
    LocationProblem.objects.create(
        location=LocationFactory(),
        description="Ceiling tile down over bench 4",
        work_order=promoted,
    )

    rows = _rows(_groups()[str(item.id)])
    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert rows[str(promoted.id)]["work_signals"]["location_problems"] == 1
    assert rows[str(promoted.id)]["verdict"] == "worked"
    assert rows[str(promoted.id)]["worked_because"] == ["location_problems"]


@pytest.mark.parametrize("copy_delay_seconds", [0, 900])
def test_an_uploaderless_photo_is_never_recorded_work(copy_delay_seconds):
    """Promotion copies the reporter's photo onto the brand-new work order.

    ``copy_to_work_order_photo`` sets no uploader and stamps ``uploaded_at``
    only AFTER the bytes have moved through the storage backend, so on remote
    storage a multi-megabyte phone photo can be stamped long after the work
    order. Timing that copy would let a slow network decide between "recorded
    work" and "cannot tell", so the classification must not consult the stamp
    at all — hence the 15-minute case as well as the instantaneous one.
    """
    user = User.objects.create_user(
        username=f"walker{copy_delay_seconds}",
        email=f"walk{copy_delay_seconds}@example.com",
        password="w-password",
    )
    item = _item("As-needed building repair", "Main Workshop Fabric")
    location = LocationFactory()
    promoted = [
        _work_order(item, created_at=BASE, due_date=None, assigned_to=user),
        _work_order(item, created_at=BASE + timedelta(seconds=90), due_date=None, assigned_to=user),
    ]
    for index, wo in enumerate(promoted):
        LocationProblem.objects.create(
            location=location,
            description=f"Problem {index}",
            photo=f"location_problems/2026/07/problem-{index}.jpg",
            work_order=wo,
        )
        photo = WorkOrderPhoto.objects.create(
            work_order=wo,
            image=f"work_order_photos/2026/07/problem-{index}.jpg",
            caption=f"From LocationProblem {index}",
        )
        WorkOrderPhoto.objects.filter(pk=photo.pk).update(
            uploaded_at=wo.created_at + timedelta(seconds=copy_delay_seconds)
        )

    rows = _rows(_groups()[str(item.id)])
    for wo in promoted:
        row = rows[str(wo.id)]
        assert row["work_signals"]["photos_total"] == 1
        assert row["work_signals"]["photos_uploaded"] == 0
        assert row["work_signals"]["photos_unattributed"] == 1
        # The promoted problem link is why it is not untouched — not the photo.
        assert row["worked_because"] == ["location_problems"]
        # The promotion link accounts for the copy, so the photo raises no
        # doubt here. The assignment doubt is never dropped by a fired finding,
        # so it must still be reported beside it.
        assert not any("uploader" in reason for reason in row["cannot_tell_because"])
        assert any("was put on this job" in reason for reason in row["cannot_tell_because"])

    text = _run()
    for wo in promoted:
        verdict_line = _verdict_line_for(text, wo)
        assert "photos" not in verdict_line
        assert "location_problems" in verdict_line
    assert "photos 0/1 uploaded (1 with no uploader)" in text


def test_an_uploaderless_photo_with_no_promotion_link_is_an_explicit_unknown(seeded):
    """Nothing accounts for it, so it is a doubt — never silent, never work."""
    item = _item("Guard inspection", "Wadkin Planer")
    plain = _work_order(item, created_at=BASE)
    orphan_photo = _work_order(item, created_at=BASE + timedelta(seconds=9))
    photo = WorkOrderPhoto.objects.create(
        work_order=orphan_photo,
        image="work_order_photos/2026/07/orphan.jpg",
        caption="Uploader since deleted",
    )
    WorkOrderPhoto.objects.filter(pk=photo.pk).update(
        uploaded_at=orphan_photo.created_at + timedelta(days=3)
    )

    rows = _rows(_groups()[str(item.id)])
    orphan_row = rows[str(orphan_photo.id)]
    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert orphan_row["work_signals"]["photos_uploaded"] == 0
    assert orphan_row["work_signals"]["photos_unattributed"] == 1
    assert orphan_row["verdict"] == "unknown"
    assert any("cannot be credited to anybody" in r for r in orphan_row["cannot_tell_because"])


def test_an_uploaderless_attachment_is_never_explained_by_a_promotion_link(seeded):
    """No promotion path writes a work-order attachment, so no link excuses one.

    ``copy_to_work_order_photo`` writes a WorkOrderPhoto, and the attachment
    copy lands on a THIRD-PARTY work order, so an uploader-less
    WorkOrderAttachment cannot be a promotion copy: every path that uploads one
    for a person stamps uploaded_by, and that FK is SET_NULL, so this is a
    genuine upload whose uploader was deleted afterwards. The doubt must
    survive the problem link that explains a copied photo.
    """
    item = _item("Sump pump service", "Grundfos Sump Pump")
    location = LocationFactory()
    plain = _work_order(item, created_at=BASE)
    promoted = _work_order(item, created_at=BASE + timedelta(seconds=12))
    LocationProblem.objects.create(location=location, description="Pump alarm", work_order=promoted)
    WorkOrderAttachment.objects.create(
        work_order=promoted,
        file="work_order_attachments/2026/07/receipt.pdf",
        kind=WorkOrderAttachment.Kind.OTHER,
        description="Uploaded by a tech whose account was deleted afterwards",
    )

    rows = _rows(_groups()[str(item.id)])
    row = rows[str(promoted.id)]

    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert row["work_signals"]["attachments_uploaded"] == 0
    assert row["work_signals"]["attachments_unattributed"] == 1
    assert row["verdict"] == "worked"
    assert row["worked_because"] == ["location_problems"]
    assert any("attachment(s) carry no uploader" in r for r in row["cannot_tell_because"])

    verdict_line = _verdict_line_for(_run(), promoted)
    assert "AND CANNOT TELL:" in verdict_line
    assert "attachment(s) carry no uploader" in verdict_line


def test_a_promoted_problem_accounts_for_one_copied_photo_and_no_more(seeded):
    """Promotion copies at most one photo, so the second one keeps its doubt."""
    item = _item("Chip conveyor clear", "Mazak QT-200")
    location = LocationFactory()
    plain = _work_order(item, created_at=BASE)
    promoted = _work_order(item, created_at=BASE + timedelta(seconds=14))
    LocationProblem.objects.create(
        location=location,
        description="Conveyor jam",
        photo="location_problems/2026/07/jam.jpg",
        work_order=promoted,
    )
    for index in range(2):
        WorkOrderPhoto.objects.create(
            work_order=promoted,
            image=f"work_order_photos/2026/07/jam-{index}.jpg",
            caption=f"Photo {index}",
        )

    rows = _rows(_groups()[str(item.id)])
    row = rows[str(promoted.id)]

    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert row["work_signals"]["photos_unattributed"] == 2
    assert row["work_signals"]["problem_photos_copied"] == 1
    assert row["worked_because"] == ["location_problems"]
    # The link accounts for ONE copy; the doubt that survives counts the rest.
    assert any(
        "1 photo(s) carry no uploader that a promoted problem link does not account for" in r
        for r in row["cannot_tell_because"]
    )


def test_a_problem_reported_without_a_photo_accounts_for_no_photo(seeded):
    """The cap counts files copied, not links that could have copied one.

    ``LocationProblem.photo`` is optional, and promotion copies nothing when it
    is empty. A text-only promoted problem must therefore buy no credit against
    an uploader-less photo — that photo is somebody's genuine upload whose
    account was deleted afterwards (uploaded_by is SET_NULL), and its doubt
    must survive.
    """
    item = _item("Extractor duct check", "Nederman Extractor")
    location = LocationFactory()
    plain = _work_order(item, created_at=BASE)
    promoted = _work_order(item, created_at=BASE + timedelta(seconds=16))
    LocationProblem.objects.create(
        location=location, description="Duct rattling, no photo taken", work_order=promoted
    )
    WorkOrderPhoto.objects.create(
        work_order=promoted,
        image="work_order_photos/2026/07/duct.jpg",
        caption="Uploader since deleted",
    )

    rows = _rows(_groups()[str(item.id)])
    row = rows[str(promoted.id)]

    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert row["work_signals"]["location_problems"] == 1
    assert row["work_signals"]["photos_unattributed"] == 1
    assert row["work_signals"]["problem_photos_copied"] == 0
    assert row["worked_because"] == ["location_problems"]
    assert any(
        "1 photo(s) carry no uploader that a promoted problem link does not account for" in r
        for r in row["cannot_tell_because"]
    )

    verdict_line = _verdict_line_for(_run(), promoted)
    assert "AND CANNOT TELL:" in verdict_line
    assert "carry no uploader" in verdict_line


def test_the_largest_burst_and_the_ranked_run_are_reported_separately(seeded):
    """Two runs of different provenance: the biggest is not the one ranked.

    Three work orders raised by hand seconds apart each write a wo_create audit
    row; two generate_work_order retries four hours later write none. The
    ranking turns on the unaudited pair, so the group's biggest run (3) and the
    run the reason quotes (2) are different populations, and each number has to
    name the rows it covers.
    """
    import csv as csv_module

    item = _item("Chuck cleaning", "Okuma Genos L250")
    by_hand = [_work_order(item, created_at=BASE + timedelta(seconds=o)) for o in (0, 5, 10)]
    for index, wo in enumerate(by_hand):
        event = MaintenanceAuditEvent.objects.create(
            action=MaintenanceAuditEvent.Action.WO_CREATE,
            actor=seeded["planner"] if index else seeded["operator"],
            work_order=wo,
        )
        MaintenanceAuditEvent.objects.filter(pk=event.pk).update(created_at=wo.created_at)
    retries = [
        _work_order(item, created_at=BASE + timedelta(hours=4)),
        _work_order(item, created_at=BASE + timedelta(hours=4, seconds=20)),
    ]

    group = _groups()[str(item.id)]
    reason = group["confidence_reason"]

    assert group["count"] == 5
    assert group["largest_burst"] == 3
    assert group["ranked_burst"] == 2
    assert group["confidence"] == "high"
    assert "none of those 2 has a WO_CREATE audit row" in reason
    assert "hold 3 row(s) with a wo_create audit row between them" in reason
    assert [_rows(group)[str(wo.id)]["created_by"] for wo in retries] == ["", ""]

    text = _run()
    assert "largest burst of ANY provenance: 3 of 5" in text
    assert "confidence ranked on a run of 2" in text

    csv_rows = [r for r in csv_module.reader(StringIO(_run("--format", "csv"))) if r]
    header = next(r for r in csv_rows if "work_order_id" in r)
    body = [
        r
        for r in csv_rows[csv_rows.index(header) + 1 :]
        if r[header.index("maintenance_item_id")] == str(item.id)
    ]
    assert len(body) == 5
    assert {r[header.index("largest_burst")] for r in body} == {"3"}
    assert {r[header.index("ranked_burst")] for r in body} == {"2"}


def test_a_photo_with_an_uploader_is_recorded_work(seeded):
    """Every path that uploads for a person stamps uploaded_by server-side."""
    item = _item("Belt guard check", "Startrite Bandsaw")
    plain = _work_order(item, created_at=BASE)
    photographed = _work_order(item, created_at=BASE + timedelta(seconds=8))
    photo = WorkOrderPhoto.objects.create(
        work_order=photographed,
        image="work_order_photos/2026/07/guard.jpg",
        caption="Guard refitted",
        uploaded_by=seeded["operator"],
    )
    # Even stamped in the same instant as the work order: the uploader decides.
    WorkOrderPhoto.objects.filter(pk=photo.pk).update(uploaded_at=photographed.created_at)

    rows = _rows(_groups()[str(item.id)])
    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert rows[str(photographed.id)]["work_signals"]["photos_uploaded"] == 1
    assert rows[str(photographed.id)]["work_signals"]["photos_unattributed"] == 0
    assert rows[str(photographed.id)]["worked_because"] == ["photos_uploaded"]


def test_every_reported_signal_is_classified_exactly_once(seeded):
    """The (A1)/(A2)/(C)/context inventory must cover every key the report emits.

    This is the invariant the categorisation exists to hold: a signal added
    later cannot slip out unclassified and quietly become "we checked and found
    nothing".
    """
    payload = _payload()
    groups = {g["maintenance_item_id"]: g for g in payload["groups"]}
    emitted = set(groups[str(seeded["item_a"].id)]["work_orders"][0]["work_signals"])

    buckets = {
        "work": set(payload["work_signals"]),
        "attachment": set(payload["attachment_signals"]),
        "indeterminate": set(payload["indeterminate_signals"]),
        "context": set(payload["context_signals"]),
    }
    classified = set().union(*buckets.values())

    assert emitted - classified == set(), "unclassified signal(s) reported"
    assert classified - emitted == set(), "classified signal(s) never reported"
    for left, right in itertools.combinations(buckets, 2):
        assert not buckets[left] & buckets[right], f"{left} and {right} overlap"

    # A typo in the suppression table would silently stop dropping an unknown a
    # finding accounts for, or drop one nothing accounts for.
    findings = buckets["work"] | buckets["attachment"]
    explained_by = payload["indeterminate_signals_explained_by"]
    assert set(explained_by) <= buckets["indeterminate"], "explains a non-(C) signal"
    for name, explainers in explained_by.items():
        assert explainers, f"{name} listed with no explainer"
        assert set(explainers) <= findings, f"{name} explained by a non-(A) signal"

    # The capped table suppresses only as much as a fired finding actually
    # accounts for, so its ceiling must be a counted signal, not a finding.
    explained_up_to = payload["indeterminate_signals_explained_up_to"]
    assert set(explained_up_to) <= buckets["indeterminate"], "caps a non-(C) signal"
    for name, rule in explained_up_to.items():
        assert rule["by"], f"{name} listed with no explainer"
        assert set(rule["by"]) <= findings, f"{name} capped by a non-(A) signal"
        assert rule["counted_by"] in buckets["context"], f"{name} cap is not a counted signal"

    # The two tables are different rules, so a name must not sit in both.
    assert not set(explained_by) & set(explained_up_to)


def test_a_missing_due_date_is_not_asserted_to_be_backend18_damage():
    """Two problems promoted onto the same as-needed item look identical to a burst.

    promote_to_standard_work_order creates the work order with no due date and
    writes no wo_create audit row, so the ranking calls it high confidence. The
    header must present the missing due date as consistent with the BACKEND-18
    path, not as proof of it.
    """
    user = User.objects.create_user(
        username="walkthrough", email="w@example.com", password="w-password"
    )
    item = _item("As-needed building repair", "Main Workshop Fabric")
    location = LocationFactory()
    promoted = [
        _work_order(item, created_at=BASE, due_date=None, assigned_to=user),
        _work_order(item, created_at=BASE + timedelta(seconds=90), due_date=None, assigned_to=user),
    ]
    for index, wo in enumerate(promoted):
        LocationProblem.objects.create(
            location=location, description=f"Problem {index}", work_order=wo
        )

    group = _groups()[str(item.id)]
    assert group["due_date_missing"] is True

    # The ranking line sits four lines under the header and reaches all three
    # formats, so it must hedge exactly as the header does — these rows were
    # promoted, not retried.
    assert group["confidence"] == "high"
    assert "CONSISTENT WITH the generate_work_order path" in group["confidence_reason"]
    assert "does not establish it" in group["confidence_reason"]
    assert "see the false-positive note" in group["confidence_reason"]
    assert "the signature of the generate_work_order" not in group["confidence_reason"]

    text = _run()
    assert "CONSISTENT WITH the dashboard Generate button" in text
    assert "but it does not establish it" in text
    assert "promoting a location" in text
    # The flat provenance claim an earlier cut printed as fact.
    assert "which is the BACKEND-18 path" not in text
    # ...and the false-positive note names both other producers, in every format.
    payload = _payload()
    assert "promoting a reported location problem" in payload["false_positive_causes"]
    assert "simply omits due_date" in payload["false_positive_causes"]


def test_an_assigned_worked_row_still_reports_the_assignment_doubt(seeded):
    """Recorded work proves SOME of the job was done, not all of the assignee's.

    So the assignment doubt is deliberately NOT dropped by a work signal, and
    a non-empty cannot_tell_because is not a claim that the row is unworked —
    the report says both things where both are true.
    """
    item = _item("Lathe way lubrication", "Harrison M300")
    plain = _work_order(item, created_at=BASE)
    assigned_and_worked = _work_order(
        item,
        created_at=BASE + timedelta(seconds=12),
        status=WorkOrder.Status.COMPLETED,
        assigned_to=seeded["operator"],
        elapsed_seconds=1800,
    )
    MaintenanceAuditEvent.objects.create(
        action=MaintenanceAuditEvent.Action.WO_COMPLETE,
        actor=seeded["operator"],
        work_order=assigned_and_worked,
    )

    rows = _rows(_groups()[str(item.id)])
    row = rows[str(assigned_and_worked.id)]
    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"

    assert row["verdict"] == "worked"
    assert "status_beyond_open" in row["worked_because"]
    # The wo_complete audit row IS explained by the completion and is dropped...
    assert row["work_signals"]["other_audit_events"] == 1
    assert not any("audit event" in r for r in row["cannot_tell_because"])
    # ...while the assignment doubt is not, and stands alongside the finding.
    assert any("assigned to" in r for r in row["cannot_tell_because"])

    verdict_line = _verdict_line_for(_run(), assigned_and_worked)
    assert verdict_line.startswith("     WORKED?     : YES")
    assert "AND CANNOT TELL:" in verdict_line
    assert "assigned to" in verdict_line


def test_a_scanned_back_form_answers_the_printed_form_unknown(seeded):
    """ "May have been worked and never scanned back" — unless it came back.

    The paired direction of the completed-work-order case: an unknown a fired
    finding genuinely accounts for must be dropped, while one it does not must
    survive.
    """
    item = _item("Extraction filter swap", "Felder AF22")
    plain = _work_order(item, created_at=BASE)
    scanned = _work_order(
        item,
        created_at=BASE + timedelta(seconds=10),
        completed_scan="work_orders/scans/2026/07/felder.pdf",
    )
    printed_only = _work_order(item, created_at=BASE + timedelta(seconds=21))
    for wo in (scanned, printed_only):
        WorkOrderOmrTemplate.objects.create(
            work_order=wo, template_version=1, page_w_pt=595.0, page_h_pt=842.0
        )

    rows = _rows(_groups()[str(item.id)])
    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"

    # The form came back, so the "never scanned back" doubt is answered.
    assert rows[str(scanned.id)]["work_signals"]["omr_templates"] == 1
    assert rows[str(scanned.id)]["worked_because"] == ["completed_scan"]
    assert rows[str(scanned.id)]["cannot_tell_because"] == []

    # Nothing accounts for this one, so the doubt stands — and with no (A)
    # signal at all the verdict itself is CANNOT TELL.
    assert rows[str(printed_only.id)]["verdict"] == "unknown"
    assert any(
        "never scanned back" in reason
        for reason in rows[str(printed_only.id)]["cannot_tell_because"]
    )


def test_bundled_sibling_pms_are_not_treated_as_work(seeded):
    """Auto-bundling attaches siblings at creation, not by anyone working the job."""
    item = _item("Coolant filter", "Doosan Lynx")
    sibling = _item("Way cover check", "Doosan Lynx")
    plain = _work_order(item, created_at=BASE)
    bundled = _work_order(item, created_at=BASE + timedelta(seconds=5))
    bundled.additional_maintenance_items.set([sibling])

    rows = _rows(_groups()[str(item.id)])
    bundled_row = rows[str(bundled.id)]
    assert rows[str(plain.id)]["verdict"] == "no-recorded-work"
    assert bundled_row["work_signals"]["bundled_items"] == 1
    assert bundled_row["verdict"] == "unknown"
    assert any("auto-bundling" in r for r in bundled_row["cannot_tell_because"])


def test_an_assigned_duplicate_is_cannot_tell_not_untouched(seeded):
    """ "We cannot tell" is a different answer from "we checked and found nothing"."""
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
    assert assigned_row["worked_because"] == []

    assert group["worked_count"] == 0
    assert group["unknown_count"] == 1
    assert group["no_recorded_work_count"] == 1

    # The sentence that authorises a deletion must not be printed here.
    assert "nothing is lost either way" not in group["suggested_keep_reason"]
    assert "nothing recorded is lost either way" not in group["suggested_keep_reason"]
    assert "CANNOT BE SHOWN to be untouched" in group["suggested_keep_reason"]

    assert "CANNOT TELL — " in _verdict_line_for(_run(), assigned)


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
    """ "No evidence of work" overclaims; the report only checked its own signals."""
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
    assert payload["total_group_count"] == 5
    assert payload["matching_group_count"] == 5
    assert payload["groups_not_shown_due_to_limit"] == 4
    assert payload["confidence_tally_selected"] == {"high": 4, "medium": 0, "low": 1}
    assert payload["selected_group_count"] == 5
    assert payload["no_groups_reason"] is None

    text = _run("--limit", "1")
    assert "Showing 1 of 5 suspected group(s) found." in text
    assert "4 further group(s) not shown because of --limit 1." in text


def test_min_confidence_filters_out_the_likely_false_positives_and_says_so(seeded):
    payload = _payload(min_confidence="high")
    shown = {g["maintenance_item_id"] for g in payload["groups"]}

    assert str(seeded["item_c"].id) not in shown
    assert str(seeded["item_a"].id) in shown

    assert payload["total_group_count"] == 5
    assert payload["matching_group_count"] == 4
    assert payload["groups_hidden_by_min_confidence"] == 1
    assert payload["groups_hidden_by_min_confidence_tally"]["low"] == 1

    text = _run("--min-confidence", "high")
    assert "Showing 4 of 5 suspected group(s) found." in text
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


def test_text_output_with_no_duplicates_states_what_it_searched_not_an_absolute():
    """ "Nothing to review" is a claim about a population, so name the population."""
    out = _run()

    assert "SUSPECTED" in out
    assert "WHAT THIS RUN SEARCHED" in out
    assert "Work orders in the database: 0." in out
    assert "NO DUPLICATE GROUP FOUND — read that strictly" in out
    assert "established absence for those rows under that key ONLY" in out
    assert "anything recorded outside OMS" in out
    # The bare absolutes an earlier cut printed instead.
    assert "Nothing to review." not in out
    assert "No (maintenance item, due date) pair has more than one work order." not in out


def test_json_output_carries_the_suspected_caveat(seeded):
    payload = json.loads(_run("--format", "json"))

    assert payload["read_only"] is True
    assert payload["suspected_not_confirmed"] is True
    assert "re-scheduled" in payload["false_positive_causes"]
    assert "quantity_used alone is NOT evidence" in payload["worked_signal_definitions"]
    assert "NOT COVERED AT ALL" in payload["coverage_caveat"]
    assert "does NOT mean" in payload["coverage_caveat"]
    # A reader filtering on cannot_tell_because must be told what it returns.
    assert "NOT a claim that the row is" in payload["worked_signal_definitions"]
    assert "EXPECTED to persist" in payload["worked_signal_definitions"]
    assert "open question here" in payload["coverage_caveat"]
    assert payload["group_count"] == 5
    assert payload["total_group_count"] == 5
    assert payload["nothing_found_note"] is None
    assert "indeterminate_signal_names" not in payload  # one concept, one key
    assert "tools_restaged" in payload["indeterminate_signals"]
    assert "tools_restaged" not in payload["definitive_signals"]
    assert "bundled_items" in payload["indeterminate_signals"]
    assert "materials_applied" in payload["definitive_signals"]


def test_csv_output_is_one_row_per_work_order_and_carries_the_caveat(seeded):
    import csv as csv_module

    text = _run("--format", "csv")
    assert "SUSPECTED duplicates only" in text.splitlines()[0]

    rows = [r for r in csv_module.reader(StringIO(text)) if r]
    preamble = [r[0] for r in rows if r[0].startswith("#")]
    assert any("Showing 5 of 5 suspected group(s) found." in line for line in preamble)
    assert any("Work orders in the database: 12." in line for line in preamble)
    assert any("NOT COVERED AT ALL" in line for line in preamble)

    header_index = next(i for i, r in enumerate(rows) if "work_order_id" in r)
    header = rows[header_index]
    body = rows[header_index + 1 :]
    assert "verdict" in header
    assert "cannot_tell_because" in header
    assert len(body) == 11  # 3 + 2 + 2 + 2 + 2 work orders

    # The no-due-date group renders as an empty cell, flagged rather than dropped.
    due_date = header.index("due_date")
    missing = header.index("due_date_missing")
    undated = [r for r in body if r[missing] == "yes"]
    assert len(undated) == 2
    assert {r[due_date] for r in undated} == {""}


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


def _offending_calls(tree):
    """Every mutating ORM call in ``tree``, as source text. The guard itself.

    Both the tripwire and its meta-test call THIS function, so narrowing the
    guard cannot leave the meta-test green while the tripwire stops catching
    writes.
    """
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
    return sorted(offenders)


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
    offenders = _offending_calls(ast.parse(COMMAND_SOURCE.read_text()))
    assert not offenders, f"mutating call(s) {offenders} must never appear in {COMMAND}"


def test_the_tripwire_would_catch_a_write_wired_into_the_command(tmp_path):
    """Exercise the guard itself, not a copy of it, on a source that must fail.

    ``_offending_calls`` is the same function the tripwire above calls, so a
    change that stops it detecting writes fails here too rather than leaving a
    green meta-test certifying a guard that no longer guards.
    """
    source = tmp_path / "with_a_write.py"
    source.write_text(
        "def handle(self, qs, seen):\n"
        "    seen = set()\n"
        "    seen.add(1)\n"  # ordinary Python — must not be an offence
        "    qs.update(status='closed')\n"  # the write the tripwire exists for
        "    qs.first().delete()\n"  # ...and one that is a write on any receiver
    )

    assert _offending_calls(ast.parse(source.read_text())) == ["qs.first().delete", "qs.update"]


def test_running_the_report_changes_nothing(seeded):
    def snapshot():
        return sorted(
            (
                str(wo.id),
                wo.status,
                wo.due_date.isoformat() if wo.due_date else None,
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
