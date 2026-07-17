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
