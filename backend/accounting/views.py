"""DRF surface for the accounting ledger.

The chart-of-accounts + trial-balance reads are staff-only (``IsAdminUser``) —
we deliberately do NOT expose hordak's own URLs/UI. Phase 2 adds committee-scoped
endpoints: :class:`CommitteeStatementView` (a committee's own SIG admin, not just
staff, can read that committee's statement) and the one *write* endpoint,
:class:`CommitteeSettlementView` (committee settlement / period-close, staff-only).
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from hordak.models import Account
from rest_framework import serializers, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from membership.services import is_owning_group_admin

from .adapters import ACCOUNT_COMMITTEE_SUPPLIES_EXPENSE, settle_committee
from .reports import (
    CommitteeStatementCSVRenderer,
    CommitteeStatementPDFRenderer,
    committee_statement,
    committee_statement_csv,
)
from .serializers import (
    AccountSerializer,
    CommitteeSettlementRequestSerializer,
    CommitteeSettlementResponseSerializer,
    CommitteeStatementReportSerializer,
)
from .services import committee_balance, trial_balance

_CENTS = Decimal("0.01")


class AccountViewSet(viewsets.ReadOnlyModelViewSet):
    """Chart of accounts with live balances. Staff/superuser only (Phase 1)."""

    serializer_class = AccountSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        return Account.objects.with_balances().order_by("full_code", "code")


class TrialBalanceView(APIView):
    """Trial balance across all accounts. Staff/superuser only (Phase 1)."""

    permission_classes = [IsAdminUser]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="as_of",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Only include transactions on or before this ISO date.",
            )
        ],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request):
        as_of = None
        as_of_raw = request.query_params.get("as_of")
        if as_of_raw:
            as_of = parse_date(as_of_raw)
            if as_of is None:
                return Response(
                    {"detail": "as_of must be an ISO date (YYYY-MM-DD)."},
                    status=400,
                )
        return Response(_serialize_trial_balance(trial_balance(as_of=as_of)))


class CommitteeSettlementView(APIView):
    """Settle (period-close) a committee's outstanding balance. **Staff only.**

    ``POST /api/accounting/committee-settlement/`` posts one append-only
    ``SETTLEMENT`` entry that zeroes the committee's **5100** balance (CR 5100 /
    DR 3000 absorbed, or DR 1200 when ``reimbursed``). This is the
    destructive-feeling close, so it is ``IsAdminUser`` (staff/superuser) — a
    SIG admin may *view* its statement but not settle. Nothing is ever edited or
    deleted; a mistaken settlement is corrected with a reversal.
    """

    permission_classes = [IsAdminUser]

    @extend_schema(
        request=CommitteeSettlementRequestSerializer,
        responses=CommitteeSettlementResponseSerializer,
    )
    def post(self, request):
        serializer = CommitteeSettlementRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        committee = get_object_or_404(Group, pk=data["committee"])
        as_of = data.get("as_of")
        reimbursed = data.get("reimbursed", False)
        note = data.get("note", "")

        txn = settle_committee(
            committee=committee,
            as_of=as_of,
            reimbursed=reimbursed,
            created_by=request.user,
            note=note,
        )
        new_balance = committee_balance(committee, as_of=as_of)

        body = {
            "reimbursed": reimbursed,
            "new_balance": str(new_balance),
            "committee": {"id": committee.id, "name": committee.name},
        }
        if txn is None:
            body["settled_amount"] = "0.00"
            body["transaction"] = None
            body["detail"] = "Nothing to settle — the committee balance is already 0.00."
        else:
            settled_leg = txn.legs.get(account__code=ACCOUNT_COMMITTEE_SUPPLIES_EXPENSE)
            body["settled_amount"] = str(settled_leg.credit.amount.quantize(_CENTS))
            body["transaction"] = str(txn.uuid)
        return Response(body)


def _serialize_trial_balance(report: dict) -> dict:
    """Render the trial-balance dict with all money values as decimal strings."""
    return {
        "accounts": [
            {
                **row,
                "debit": str(row["debit"]),
                "credit": str(row["credit"]),
                "balance": str(row["balance"]),
            }
            for row in report["accounts"]
        ],
        "total_debit": str(report["total_debit"]),
        "total_credit": str(report["total_credit"]),
        "balanced": report["balanced"],
    }


_PERIOD_PRESETS = {
    "past_week": timedelta(days=7),
    "past_month": timedelta(days=30),
    "past_year": timedelta(days=365),
}


class CommitteeStatementView(APIView):
    """Per-committee (SIG) statement over the ledger. JSON / CSV / PDF.

    Select a committee + a period and get its statement: every ledger line
    attributed to that committee in the window (the ``SIG_CHARGE`` consumption
    from Bead 1 today; purchases/settlements once later beads land), with a
    running balance and source-type totals. Mirrors the asset cost-recovery
    report's structure and content-negotiation.

    Query params:
    - ``committee`` (required): the ``auth.Group`` (SIG) id.
    - Period (one of): ``period`` in {past_week, past_month, past_year}
      (trailing window ending today) OR ``start`` & ``end`` (YYYY-MM-DD).
    - ``format`` in {json (default), csv, pdf}.

    Permissions: authenticated; **staff/superuser** may read any committee; a
    non-staff user must be an **admin of the requested committee**
    (``membership.services.is_owning_group_admin``), else 403. A missing/invalid
    ``committee`` is 400, an unknown one 404.
    """

    permission_classes = [IsAuthenticated]
    renderer_classes = [
        JSONRenderer,
        CommitteeStatementCSVRenderer,
        CommitteeStatementPDFRenderer,
    ]

    @staticmethod
    def _resolve_committee(request) -> Group:
        """Resolve the ``committee`` query param to a ``Group`` (400/404)."""
        raw = request.query_params.get("committee")
        if not raw:
            raise serializers.ValidationError(
                {"committee": "This query parameter is required (a Group id)."}
            )
        try:
            committee_id = int(raw)
        except (TypeError, ValueError):
            raise serializers.ValidationError({"committee": f"Invalid committee id: {raw!r}."})
        try:
            return Group.objects.get(pk=committee_id)
        except Group.DoesNotExist:
            raise NotFound(f"No committee (Group) with id {committee_id}.")

    @staticmethod
    def _window(request):
        """Resolve the reporting window from ``period`` or ``start``/``end``.

        Mirrors ``AssetReportViewSet._cost_recovery_window`` (preset trailing
        windows of 7/30/365 days, or an explicit ``YYYY-MM-DD`` range). Returns
        ``(start_date, end_date, period_label)`` — ``period_label`` is the preset
        name, or ``None`` for a custom range.
        """
        today = timezone.now().date()

        period = request.query_params.get("period")
        if period:
            span = _PERIOD_PRESETS.get(period)
            if span is None:
                raise serializers.ValidationError(
                    {"period": "Must be one of past_week, past_month, past_year."}
                )
            return today - span, today, period

        start_str = request.query_params.get("start")
        end_str = request.query_params.get("end")
        if start_str and end_str:
            try:
                start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
            except ValueError:
                raise serializers.ValidationError({"start": "start and end must be YYYY-MM-DD."})
            if start_date > end_date:
                raise serializers.ValidationError({"start": "start must not be after end."})
            return start_date, end_date, None

        raise serializers.ValidationError(
            "Provide either 'period' (past_week/past_month/past_year) or both "
            "'start' and 'end' (YYYY-MM-DD)."
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("committee", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True),
            OpenApiParameter("period", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("start", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("end", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("format", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
        ],
        responses=CommitteeStatementReportSerializer,
    )
    def get(self, request):
        committee = self._resolve_committee(request)

        user = request.user
        is_staff = bool(user.is_staff or user.is_superuser)
        if not is_staff and not is_owning_group_admin(user, committee):
            raise PermissionDenied("You must be an admin of this committee to view its statement.")

        start_date, end_date, period_label = self._window(request)

        # ``format`` is DRF's reserved content-negotiation query param; the
        # renderers above register json/csv/pdf so it negotiates cleanly (an
        # unknown format 404s in negotiation before we get here).
        fmt = request.accepted_renderer.format

        report = committee_statement(
            committee=committee, start=start_date, end=end_date, period=period_label
        )

        if fmt == "csv":
            return committee_statement_csv(report)

        if fmt == "pdf":
            from .utils.committee_statement_pdf import generate_committee_statement_pdf

            pdf_bytes = generate_committee_statement_pdf(report)
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = 'attachment; filename="committee_statement.pdf"'
            return response

        return Response(CommitteeStatementReportSerializer(report).data)
