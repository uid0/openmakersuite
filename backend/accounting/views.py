"""Read-only, staff-only DRF surface for the accounting ledger (Phase 1).

Committee-scoped reads arrive in Phase 2; for now everything is
``IsAdminUser``. We deliberately do NOT expose hordak's own URLs/UI — these are
OMS-native endpoints over the chart of accounts and the trial balance.

Phase 2 · Bead 4 adds the one *write* endpoint here: committee settlement /
period-close (:class:`CommitteeSettlementView`), still staff-only.
"""

from decimal import Decimal

from django.contrib.auth.models import Group
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from hordak.models import Account
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .adapters import ACCOUNT_COMMITTEE_SUPPLIES_EXPENSE, settle_committee
from .serializers import (
    AccountSerializer,
    CommitteeSettlementRequestSerializer,
    CommitteeSettlementResponseSerializer,
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
