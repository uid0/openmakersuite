"""Read-only, staff-only DRF surface for the accounting ledger (Phase 1).

Committee-scoped reads arrive in Phase 2; for now everything is
``IsAdminUser``. We deliberately do NOT expose hordak's own URLs/UI — these are
OMS-native endpoints over the chart of accounts and the trial balance.
"""

from django.utils.dateparse import parse_date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from hordak.models import Account
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import AccountSerializer
from .services import trial_balance


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
