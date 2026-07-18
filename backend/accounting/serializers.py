from decimal import Decimal

from hordak.models import Account
from rest_framework import serializers

from .services import CURRENCY, type_word

_CENTS = Decimal("0.01")


class AccountSerializer(serializers.ModelSerializer):
    """Chart-of-accounts row with its live balance as a USD decimal string."""

    type = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = ["uuid", "code", "full_code", "name", "type", "balance"]

    def get_type(self, obj) -> str:
        return type_word(obj)

    def get_balance(self, obj) -> str:
        # ``balance`` is annotated when the queryset uses ``with_balances()``;
        # fall back to a per-row query when the serializer is used standalone.
        balance = getattr(obj, "balance", None)
        if balance is None:
            balance = obj.get_balance()
        return str(balance[CURRENCY].amount.quantize(_CENTS))


class CommitteeStatementLineSerializer(serializers.Serializer):
    """One ledger line in a committee (SIG) statement.

    ``debit``/``credit`` is null on the side the line does not use; ``amount``
    is the signed net effect on the committee (a debit is positive), and
    ``running_balance`` is the cumulative net through this line. Every money
    field renders as a USD decimal string.
    """

    date = serializers.DateField()
    source_type = serializers.CharField(allow_blank=True)
    account_code = serializers.CharField()
    account_name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    debit = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    credit = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    running_balance = serializers.DecimalField(max_digits=12, decimal_places=2)


class CommitteeStatementTotalsSerializer(serializers.Serializer):
    """Period totals bucketed by source type; ``net`` is the ending balance."""

    consumed = serializers.DecimalField(max_digits=12, decimal_places=2)
    purchased = serializers.DecimalField(max_digits=12, decimal_places=2)
    settled = serializers.DecimalField(max_digits=12, decimal_places=2)
    net = serializers.DecimalField(max_digits=12, decimal_places=2)


class CommitteeStatementCommitteeSerializer(serializers.Serializer):
    """The committee (``auth.Group``) a statement is drawn for."""

    id = serializers.IntegerField()
    name = serializers.CharField()


class CommitteeStatementReportSerializer(serializers.Serializer):
    """The full committee statement payload (mirrors the cost-recovery report).

    ``totals['net']`` is the recoverable/owed net for the committee over the
    window; ``lines`` is the itemized ledger activity backing it.
    """

    committee = CommitteeStatementCommitteeSerializer()
    period = serializers.CharField(allow_null=True)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    generated_at = serializers.DateTimeField()
    lines = CommitteeStatementLineSerializer(many=True)
    totals = CommitteeStatementTotalsSerializer()
