"""Tests for the read-only ``report_duplicate_work_orders`` management command.

The seeded data deliberately contains all three shapes the captain needs the
report to tell apart:

* a real BACKEND-18 retry cluster (three work orders seconds apart, untouched);
* a cluster where one of the duplicates has since been worked;
* a legitimately repeated PM raised twice, days apart, through the audited
  create path — which shares the (maintenance_item, due_date) grouping key and
  must therefore be flagged as a likely false positive rather than hidden.
"""

import json
import pathlib
from datetime import date, datetime, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

import pytest

from inventory.models import (
    MaintenanceAuditEvent,
    MaintenanceItem,
    MaintenanceTask,
    WorkOrder,
    WorkOrderLotoCompletion,
    WorkOrderTaskCompletion,
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
    """Three groups: retry cluster, worked cluster, legitimate repeat."""
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
        "retry": retry,
        "untouched": untouched,
        "worked": worked,
        "legit": [legit_first, legit_second],
    }


def _groups(**kwargs):
    out = StringIO()
    call_command(COMMAND, "--format", "json", stdout=out, **kwargs)
    return {g["maintenance_item_id"]: g for g in json.loads(out.getvalue())["groups"]}


# --------------------------------------------------------------- the report


def test_retry_cluster_is_reported_as_high_confidence(seeded):
    group = _groups()[str(seeded["item_a"].id)]

    assert group["count"] == 3
    assert group["confidence"] == "high"
    assert group["largest_burst"] == 3
    assert group["worked_count"] == 0
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
    assert first["number"] == f"WO-{str(seeded['retry'][0].id)[:8]}"
    assert first["status"] == "open"
    assert first["created_at"].startswith("2026-07-01T09:00")
    # generate_work_order writes no audit row, so there is no creator to name.
    assert first["created_by"] == ""
    assert "not recorded" in first["created_by_source"]


def test_worked_work_order_is_distinguished_from_its_untouched_duplicate(seeded):
    group = _groups()[str(seeded["item_b"].id)]
    rows = {row["id"]: row for row in group["work_orders"]}

    worked = rows[str(seeded["worked"].id)]
    untouched = rows[str(seeded["untouched"].id)]

    assert worked["worked"] is True
    assert worked["work_signals"]["tasks_completed"] == 1
    assert worked["work_signals"]["time_seconds"] == 2700
    assert worked["work_signals"]["completed_by"] == "A. Tech"
    assert worked["work_signals"]["status_beyond_open"] is True

    assert untouched["worked"] is False
    assert untouched["work_signals"]["tasks_completed"] == 0
    assert untouched["work_signals"]["time_seconds"] == 0

    assert group["worked_count"] == 1
    assert group["suggested_keep_id"] == str(seeded["worked"].id)
    assert "only work order" in group["suggested_keep_reason"]


def test_prefilled_material_quantity_is_not_treated_as_work(seeded):
    """generate_work_order pre-fills quantity_used, so it cannot mean 'used'."""
    from decimal import Decimal

    from inventory.models import MaintenanceMaterial, WorkOrderMaterialUsage

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
    for row in group["work_orders"]:
        assert row["work_signals"]["materials_total"] == 1
        assert row["work_signals"]["materials_used"] == 0
        assert row["work_signals"]["qty_edited"] == 0
        assert row["work_signals"]["materials_applied"] == 0


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

    # Ranked low, but still present — ranking, never silent exclusion.
    assert str(seeded["item_c"].id) in _groups()


def test_high_confidence_groups_are_listed_before_low_confidence_ones(seeded):
    out = StringIO()
    call_command(COMMAND, "--format", "json", stdout=out)
    order = [g["confidence"] for g in json.loads(out.getvalue())["groups"]]
    assert order == sorted(order, key=lambda c: {"high": 0, "medium": 1, "low": 2}[c])


def test_min_confidence_filters_out_the_likely_false_positives(seeded):
    high_only = _groups(min_confidence="high")
    assert str(seeded["item_c"].id) not in high_only
    assert str(seeded["item_a"].id) in high_only


def test_work_orders_without_a_duplicate_partner_are_not_reported(seeded):
    assert str(seeded["item_d"].id) not in _groups()


def test_window_seconds_controls_burst_detection(seeded):
    # Widened to a fortnight, even the five-day-apart legitimate repeat clusters.
    group = _groups(window_seconds=14 * 86400)[str(seeded["item_c"].id)]
    assert group["largest_burst"] == 2
    # ...but it still is not called high confidence, because both rows carry a
    # WO_CREATE audit row and so came through the audited create path.
    assert group["confidence"] == "medium"


def test_since_narrows_the_window(seeded):
    groups = _groups(since="2026-07-01")
    assert str(seeded["item_a"].id) in groups
    assert str(seeded["item_c"].id) not in groups


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
    assert payload["group_count"] == 3


def test_csv_output_is_one_row_per_work_order_and_carries_the_caveat(seeded):
    import csv as csv_module

    text = _run("--format", "csv")
    assert "SUSPECTED duplicates only" in text.splitlines()[0]

    rows = list(csv_module.reader(StringIO(text)))
    header = rows[1]
    body = rows[2:]
    assert "work_order_id" in header
    assert len([r for r in body if r]) == 7  # 3 + 2 + 2 work orders


def test_limit_caps_the_number_of_groups(seeded):
    payload = json.loads(_run("--format", "json", "--limit", "1"))
    assert payload["group_count"] == 1


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


def test_command_source_contains_no_mutating_orm_calls():
    """Structural guard: nobody wires cleanup into this command later.

    Walks the AST rather than grepping, so the help text and docstrings are
    free to *name* the forbidden calls while the code may not make them.
    """
    import ast

    forbidden = {
        "save",
        "delete",
        "update",
        "create",
        "get_or_create",
        "update_or_create",
        "bulk_create",
        "bulk_update",
        "raw",
        "execute",
        "add",
        "remove",
        "set",
        "clear",
    }
    tree = ast.parse(COMMAND_SOURCE.read_text())
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    offenders = sorted(called & forbidden)
    assert not offenders, f"mutating call(s) {offenders} must never appear in {COMMAND}"


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
        MaintenanceAuditEvent.objects.count(),
        MaintenanceItem.objects.count(),
    ) == counts_before
