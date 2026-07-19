"""Treasurer-readable reports over the committee (SIG) ledger.

Phase 2 · Bead 3 ships the first one: :func:`committee_statement`, the
per-committee statement over the double-entry ledger — what a committee
**consumed** (the ``SIG_CHARGE`` entries from Bead 1), with a running balance
and a period filter.

It mirrors the asset cost-recovery report
(``inventory.views.AssetReportViewSet.cost_recovery``) in structure: a pure
builder returning a plain dict, passthrough renderers so DRF's reserved
``?format=csv|pdf`` content-negotiates cleanly, and a flat one-row-per-line CSV
writer. The PDF generator lives in :mod:`accounting.utils.committee_statement_pdf`.
"""

import csv
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone

from rest_framework.renderers import BaseRenderer

from .models import LegDimension, SourceType

_CENTS = Decimal("0.01")
_ZERO = Decimal("0.00")

#: ``source_type`` -> the totals bucket it accumulates into. Only ``SIG_CHARGE``
#: is produced today (Bead 1); ``PO_RECEIPT`` / ``SETTLEMENT`` light up
#: automatically once Beads 4-5 post committee-attributed purchase / settlement
#: entries. Any other source_type (e.g. a ``REVERSAL`` of a charge) lands in no
#: named bucket but still moves ``net`` via the running balance.
_TOTALS_BUCKET = {
    SourceType.SIG_CHARGE.value: "consumed",
    SourceType.PO_RECEIPT.value: "purchased",
    SourceType.SETTLEMENT.value: "settled",
}


def _q(value) -> Decimal:
    """Coerce ``value`` to a 2dp ``Decimal`` (USD cents)."""
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return amount.quantize(_CENTS)


def committee_statement(*, committee, start, end, period=None) -> dict:
    """Build the committee (SIG) statement over the ledger for a date window.

    Queries every :class:`~accounting.models.LegDimension` attributed to
    ``committee`` whose transaction ``date`` falls in ``[start, end]``
    (inclusive), ordered by date, and emits one row per ledger line with a
    running balance of the committee's net position (debit increases it, credit
    decreases it). Totals are bucketed by ``source_type`` — ``consumed``
    (``SIG_CHARGE``), plus the forward-compatible ``purchased`` (``PO_RECEIPT``)
    and ``settled`` (``SETTLEMENT``) buckets that stay ``0.00`` until later beads
    post those entries. ``net`` is the final running balance, so it also nets out
    any reversals/adjustments that carry the committee dimension.

    Args:
        committee: the ``auth.Group`` (SIG) to report on.
        start: inclusive lower ``date`` bound of the reporting window.
        end: inclusive upper ``date`` bound of the reporting window.
        period: optional preset label (``past_week`` / ``past_month`` /
            ``past_year``) recorded on the report; ``None`` for a custom range.

    Returns:
        A dict mirroring the cost-recovery report shape::

            {committee: {id, name}, period, start_date, end_date, generated_at,
             lines: [{date, source_type, account_code, account_name,
                      description, debit, credit, amount, running_balance}, ...],
             totals: {consumed, purchased, settled, net}}

        Money values are raw ``Decimal`` (a line's ``debit``/``credit`` is
        ``None`` on the side it does not use). The API layer stringifies them for
        JSON; the CSV/PDF writers format them.
    """
    dimensions = (
        LegDimension.objects.filter(
            sig=committee,
            leg__transaction__date__range=(start, end),
        )
        .select_related(
            "leg",
            "leg__account",
            "leg__transaction",
            "leg__transaction__meta",
        )
        .order_by("leg__transaction__date", "leg__transaction_id", "leg_id")
    )

    lines = []
    running = _ZERO
    totals = {"consumed": _ZERO, "purchased": _ZERO, "settled": _ZERO, "net": _ZERO}

    for dimension in dimensions:
        leg = dimension.leg
        txn = leg.transaction
        # ``meta`` is a reverse OneToOne; Django's descriptor raises an
        # AttributeError subclass when absent, so ``getattr(..., None)`` is safe.
        meta = getattr(txn, "meta", None)
        source_type = meta.source_type if meta is not None else ""

        debit = _q(leg.debit.amount) if leg.debit is not None else None
        credit = _q(leg.credit.amount) if leg.credit is not None else None
        amount = (debit or _ZERO) - (credit or _ZERO)
        running += amount

        bucket = _TOTALS_BUCKET.get(source_type)
        if bucket is not None:
            totals[bucket] += amount

        lines.append(
            {
                "date": txn.date,
                "source_type": source_type,
                "account_code": leg.account.code,
                "account_name": leg.account.name,
                "description": leg.description or txn.description or "",
                "debit": debit,
                "credit": credit,
                "amount": amount,
                "running_balance": running,
            }
        )

    totals["net"] = running

    return {
        "committee": {"id": committee.id, "name": committee.name},
        "period": period,
        "start_date": start,
        "end_date": end,
        "generated_at": timezone.now(),
        "lines": lines,
        "totals": totals,
    }


class CommitteeStatementCSVRenderer(BaseRenderer):
    """Passthrough renderer registering the ``csv`` format for content negotiation.

    ``CommitteeStatementView`` returns a fully-formed ``HttpResponse`` for the
    CSV/PDF formats, so ``render`` is never invoked — these renderers exist only
    so DRF accepts ``?format=csv`` / ``?format=pdf`` (its reserved format query
    param) instead of 404-ing on an unregistered format.
    """

    media_type = "text/csv"
    format = "csv"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class CommitteeStatementPDFRenderer(BaseRenderer):
    """Passthrough renderer registering the ``pdf`` format (see CSV renderer)."""

    media_type = "application/pdf"
    format = "pdf"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


def _money_cell(value) -> str:
    """Render a ``Decimal`` cell as a plain 2dp string; blank for ``None``."""
    if value is None:
        return ""
    return f"{value:.2f}"


def committee_statement_csv(report: dict) -> HttpResponse:
    """Flat one-row-per-line CSV for the committee statement.

    Columns: ``date, source_type, account, description, debit, credit,
    running_balance``. An empty statement emits just the header row.
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="committee_statement.csv"'
    fieldnames = [
        "date",
        "source_type",
        "account",
        "description",
        "debit",
        "credit",
        "running_balance",
    ]
    writer = csv.DictWriter(response, fieldnames=fieldnames)
    writer.writeheader()
    for line in report["lines"]:
        writer.writerow(
            {
                "date": line["date"].isoformat(),
                "source_type": line["source_type"],
                "account": line["account_code"],
                "description": line["description"],
                "debit": _money_cell(line["debit"]),
                "credit": _money_cell(line["credit"]),
                "running_balance": _money_cell(line["running_balance"]),
            }
        )
    return response
