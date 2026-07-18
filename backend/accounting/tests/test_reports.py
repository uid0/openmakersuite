"""Committee statement report — builder + JSON/CSV/PDF endpoint.

Postgres/hordak required (the ledger uses Postgres balance functions), so the
whole module is ``django_db`` and runs on the omstest Postgres, not sqlite.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group
from django.utils import timezone

import pytest

from accounting.adapters import post_supply_consumption
from accounting.reports import committee_statement
from accounting.services import reverse_entry

pytestmark = pytest.mark.django_db

STATEMENT_URL = "/api/accounting/committee-statement/"


def _charge(committee, amount, day, ref, **kwargs):
    """Post one SIG_CHARGE for ``committee`` dated ``day`` and return its txn."""
    return post_supply_consumption(
        committee=committee,
        amount=Decimal(amount),
        source_ref=ref,
        date=day,
        **kwargs,
    )


# ── builder ──────────────────────────────────────────────────────────────────


def test_builder_lines_running_balance_and_totals():
    committee = Group.objects.create(name="Woodshop SIG")
    _charge(committee, "10.00", date(2026, 1, 10), "usage:1")
    _charge(committee, "5.00", date(2026, 1, 12), "usage:2")
    _charge(committee, "7.00", date(2026, 1, 15), "usage:3")

    report = committee_statement(committee=committee, start=date(2026, 1, 1), end=date(2026, 1, 31))

    assert report["committee"] == {"id": committee.id, "name": "Woodshop SIG"}
    assert report["period"] is None
    assert report["start_date"] == date(2026, 1, 1)
    assert report["end_date"] == date(2026, 1, 31)

    # Lines are ordered by date; each SIG_CHARGE is a debit to 5100.
    assert [line["date"] for line in report["lines"]] == [
        date(2026, 1, 10),
        date(2026, 1, 12),
        date(2026, 1, 15),
    ]
    for line in report["lines"]:
        assert line["source_type"] == "SIG_CHARGE"
        assert line["account_code"] == "5100"
        assert line["account_name"] == "Committee supplies expense"
        assert line["credit"] is None
    assert [line["debit"] for line in report["lines"]] == [
        Decimal("10.00"),
        Decimal("5.00"),
        Decimal("7.00"),
    ]
    assert [line["amount"] for line in report["lines"]] == [
        Decimal("10.00"),
        Decimal("5.00"),
        Decimal("7.00"),
    ]
    assert [line["running_balance"] for line in report["lines"]] == [
        Decimal("10.00"),
        Decimal("15.00"),
        Decimal("22.00"),
    ]
    assert report["totals"] == {
        "consumed": Decimal("22.00"),
        "purchased": Decimal("0.00"),
        "settled": Decimal("0.00"),
        "net": Decimal("22.00"),
    }


def test_builder_respects_window():
    committee = Group.objects.create(name="Metal SIG")
    _charge(committee, "10.00", date(2026, 1, 5), "usage:before")  # before window
    _charge(committee, "20.00", date(2026, 2, 10), "usage:inside")  # inside
    _charge(committee, "30.00", date(2026, 3, 20), "usage:after")  # after window

    report = committee_statement(committee=committee, start=date(2026, 2, 1), end=date(2026, 2, 28))

    assert len(report["lines"]) == 1
    assert report["lines"][0]["debit"] == Decimal("20.00")
    assert report["totals"]["consumed"] == Decimal("20.00")
    assert report["totals"]["net"] == Decimal("20.00")


def test_builder_scopes_to_requested_committee():
    a = Group.objects.create(name="SIG A")
    b = Group.objects.create(name="SIG B")
    _charge(a, "10.00", date(2026, 1, 10), "usage:a")
    _charge(b, "99.00", date(2026, 1, 11), "usage:b")

    report = committee_statement(committee=a, start=date(2026, 1, 1), end=date(2026, 1, 31))

    assert len(report["lines"]) == 1
    assert report["lines"][0]["debit"] == Decimal("10.00")
    assert report["totals"]["net"] == Decimal("10.00")


def test_builder_reversal_nets_out_but_consumed_is_gross():
    committee = Group.objects.create(name="Reversal SIG")
    txn = _charge(committee, "12.00", date(2026, 1, 10), "usage:rev")
    reverse_entry(txn, date=date(2026, 1, 11))

    report = committee_statement(committee=committee, start=date(2026, 1, 1), end=date(2026, 1, 31))

    assert len(report["lines"]) == 2
    charge_line, reversal_line = report["lines"]
    assert charge_line["source_type"] == "SIG_CHARGE"
    assert charge_line["debit"] == Decimal("12.00")
    assert reversal_line["source_type"] == "REVERSAL"
    assert reversal_line["credit"] == Decimal("12.00")
    assert reversal_line["debit"] is None
    assert reversal_line["amount"] == Decimal("-12.00")
    # Gross consumed is the charge; net is zero after the reversal.
    assert report["totals"]["consumed"] == Decimal("12.00")
    assert report["totals"]["net"] == Decimal("0.00")
    assert report["lines"][-1]["running_balance"] == Decimal("0.00")


def test_builder_empty_statement():
    committee = Group.objects.create(name="Quiet SIG")

    report = committee_statement(committee=committee, start=date(2026, 1, 1), end=date(2026, 1, 31))

    assert report["lines"] == []
    assert report["totals"] == {
        "consumed": Decimal("0.00"),
        "purchased": Decimal("0.00"),
        "settled": Decimal("0.00"),
        "net": Decimal("0.00"),
    }


# ── endpoint: permissions ────────────────────────────────────────────────────


def test_endpoint_rejects_anonymous(api_client):
    committee = Group.objects.create(name="Anon SIG")
    resp = api_client.get(STATEMENT_URL, {"committee": committee.id, "period": "past_month"})
    assert resp.status_code in (401, 403)


def test_endpoint_staff_sees_any_committee(api_client, admin_user):
    committee = Group.objects.create(name="Staff-view SIG")
    _charge(committee, "10.00", timezone.now().date(), "usage:staff")

    api_client.force_authenticate(admin_user)
    resp = api_client.get(STATEMENT_URL, {"committee": committee.id, "period": "past_year"})

    assert resp.status_code == 200
    data = resp.data
    assert data["committee"] == {"id": committee.id, "name": "Staff-view SIG"}
    assert data["period"] == "past_year"
    assert data["generated_at"]  # present + serialized
    assert len(data["lines"]) == 1
    assert data["lines"][0]["debit"] == "10.00"  # decimal string
    assert data["lines"][0]["running_balance"] == "10.00"
    assert data["totals"]["consumed"] == "10.00"
    assert data["totals"]["net"] == "10.00"


def test_endpoint_sig_admin_sees_own_committee(api_client):
    from membership.models import SIGAdmin
    from membership.tests.factories import UserFactory

    committee = Group.objects.create(name="Own SIG")
    user = UserFactory(is_staff=False)
    SIGAdmin.objects.create(user=user, group=committee, is_active=True)
    _charge(committee, "4.00", timezone.now().date(), "usage:own")

    api_client.force_authenticate(user)
    resp = api_client.get(STATEMENT_URL, {"committee": committee.id, "period": "past_month"})

    assert resp.status_code == 200
    assert resp.data["totals"]["consumed"] == "4.00"


def test_endpoint_non_admin_forbidden(api_client):
    from membership.tests.factories import UserFactory

    committee = Group.objects.create(name="Guarded SIG")
    user = UserFactory(is_staff=False)  # not an admin of committee

    api_client.force_authenticate(user)
    resp = api_client.get(STATEMENT_URL, {"committee": committee.id, "period": "past_month"})

    assert resp.status_code == 403


def test_endpoint_admin_of_other_committee_forbidden(api_client):
    """The gate is per-committee: admin of SIG X cannot read SIG Y."""
    from membership.models import SIGAdmin
    from membership.tests.factories import UserFactory

    own = Group.objects.create(name="Their SIG")
    other = Group.objects.create(name="Not their SIG")
    user = UserFactory(is_staff=False)
    SIGAdmin.objects.create(user=user, group=own, is_active=True)

    api_client.force_authenticate(user)
    resp = api_client.get(STATEMENT_URL, {"committee": other.id, "period": "past_month"})

    assert resp.status_code == 403


# ── endpoint: validation ─────────────────────────────────────────────────────


def test_endpoint_missing_committee_400(api_client, admin_user):
    api_client.force_authenticate(admin_user)
    resp = api_client.get(STATEMENT_URL, {"period": "past_month"})
    assert resp.status_code == 400


def test_endpoint_invalid_committee_400(api_client, admin_user):
    api_client.force_authenticate(admin_user)
    resp = api_client.get(STATEMENT_URL, {"committee": "not-an-int", "period": "past_month"})
    assert resp.status_code == 400


def test_endpoint_unknown_committee_404(api_client, admin_user):
    api_client.force_authenticate(admin_user)
    resp = api_client.get(STATEMENT_URL, {"committee": 999999, "period": "past_month"})
    assert resp.status_code == 404


def test_endpoint_bad_period_400(api_client, admin_user):
    committee = Group.objects.create(name="Bad-period SIG")
    api_client.force_authenticate(admin_user)
    resp = api_client.get(STATEMENT_URL, {"committee": committee.id, "period": "past_decade"})
    assert resp.status_code == 400


def test_endpoint_requires_period_or_range_400(api_client, admin_user):
    committee = Group.objects.create(name="No-window SIG")
    api_client.force_authenticate(admin_user)
    resp = api_client.get(STATEMENT_URL, {"committee": committee.id})
    assert resp.status_code == 400


def test_endpoint_custom_range(api_client, admin_user):
    committee = Group.objects.create(name="Range SIG")
    _charge(committee, "6.00", date(2026, 2, 10), "usage:range")

    api_client.force_authenticate(admin_user)
    resp = api_client.get(
        STATEMENT_URL,
        {"committee": committee.id, "start": "2026-02-01", "end": "2026-02-28"},
    )

    assert resp.status_code == 200
    assert resp.data["period"] is None
    assert resp.data["start_date"] == "2026-02-01"
    assert resp.data["end_date"] == "2026-02-28"
    assert resp.data["totals"]["consumed"] == "6.00"


def test_endpoint_bad_custom_range_400(api_client, admin_user):
    committee = Group.objects.create(name="Bad-range SIG")
    api_client.force_authenticate(admin_user)
    resp = api_client.get(
        STATEMENT_URL,
        {"committee": committee.id, "start": "2026-03-01", "end": "2026-02-01"},
    )
    assert resp.status_code == 400


# ── endpoint: CSV / PDF ──────────────────────────────────────────────────────


def test_endpoint_csv(api_client, admin_user):
    committee = Group.objects.create(name="CSV SIG")
    _charge(committee, "10.00", date(2026, 2, 10), "usage:csv1")
    _charge(committee, "5.00", date(2026, 2, 12), "usage:csv2")

    api_client.force_authenticate(admin_user)
    resp = api_client.get(
        STATEMENT_URL,
        {
            "committee": committee.id,
            "start": "2026-02-01",
            "end": "2026-02-28",
            "format": "csv",
        },
    )

    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert "committee_statement.csv" in resp["Content-Disposition"]
    body = resp.content.decode()
    assert "date,source_type,account,description,debit,credit,running_balance" in body
    assert "SIG_CHARGE" in body
    assert "5100" in body
    assert "15.00" in body  # running balance accumulates 10 -> 15


def test_endpoint_pdf(api_client, admin_user):
    committee = Group.objects.create(name="PDF SIG")
    _charge(committee, "10.00", date(2026, 2, 10), "usage:pdf")

    api_client.force_authenticate(admin_user)
    resp = api_client.get(
        STATEMENT_URL,
        {
            "committee": committee.id,
            "start": "2026-02-01",
            "end": "2026-02-28",
            "format": "pdf",
        },
    )

    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert "committee_statement.pdf" in resp["Content-Disposition"]
    assert resp.content[:5] == b"%PDF-"
    assert len(resp.content) > 1000


def test_endpoint_pdf_empty_statement(api_client, admin_user):
    committee = Group.objects.create(name="Empty PDF SIG")
    api_client.force_authenticate(admin_user)
    resp = api_client.get(
        STATEMENT_URL,
        {"committee": committee.id, "period": "past_month", "format": "pdf"},
    )
    assert resp.status_code == 200
    assert resp.content[:5] == b"%PDF-"


# ── units: passthrough renderers + PDF helper branches ───────────────────────


def test_passthrough_renderers_return_data_unchanged():
    """The CSV/PDF renderers only register the format; ``render`` is a no-op
    passthrough (the view returns a ready HttpResponse for those formats)."""
    from accounting.reports import (
        CommitteeStatementCSVRenderer,
        CommitteeStatementPDFRenderer,
    )

    sentinel = object()
    assert CommitteeStatementCSVRenderer().render(sentinel) is sentinel
    assert CommitteeStatementPDFRenderer().render(sentinel) is sentinel
    assert CommitteeStatementCSVRenderer.format == "csv"
    assert CommitteeStatementPDFRenderer.format == "pdf"


def test_pdf_generator_covers_preset_and_nondate_and_escaping():
    """Exercise the PDF helper branches without a DB round-trip: a preset period
    label, a non-date ``generated_at`` (str fallback), a REVERSAL source label,
    and an ``&``/``<`` in a description (paragraph escaping)."""
    from accounting.utils.committee_statement_pdf import generate_committee_statement_pdf

    report = {
        "committee": {"id": 1, "name": "Unit SIG"},
        "period": "past_week",  # preset branch in _period_display
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 1, 7),
        "generated_at": "2026-01-07",  # str -> _fmt_date str fallback
        "lines": [
            {
                "date": date(2026, 1, 3),
                "source_type": "REVERSAL",  # _source_label alias branch
                "account_code": "5100",
                "account_name": "Committee supplies expense",
                "description": "reverse x & y < z",  # escaped in _para
                "debit": None,
                "credit": Decimal("3.00"),
                "amount": Decimal("-3.00"),
                "running_balance": Decimal("-3.00"),
            }
        ],
        "totals": {
            "consumed": Decimal("0.00"),
            "purchased": Decimal("0.00"),
            "settled": Decimal("0.00"),
            "net": Decimal("-3.00"),
        },
    }

    pdf = generate_committee_statement_pdf(report)
    assert pdf[:5] == b"%PDF-"
