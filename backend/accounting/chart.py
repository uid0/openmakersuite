"""The Phase-1 chart of accounts and its idempotent seed.

This is the single source of truth used by BOTH the ``seed_chart_of_accounts``
management command and the ``0002_seed_chart_of_accounts`` data migration, so
the two can never drift. Every account is a **root** account (no hierarchy yet)
in a single currency (USD). There is intentionally **no 1000 Cash account** in
Phase 1 — that is deferred.
"""

from hordak.models import Account, AccountType

# (code, name, type-word). ``type`` maps to hordak's AccountType member names
# (asset / liability / income / expense / equity). Keep ``code`` <= 6 chars.
CHART_OF_ACCOUNTS = [
    ("1200", "Accounts Receivable", "asset"),
    ("1300", "Inventory — Supplies on hand", "asset"),
    ("1700", "Equipment / Fixed assets", "asset"),
    ("2000", "Accounts Payable", "liability"),
    ("3000", "Net assets / Fund balance", "equity"),
    ("4000", "Donation income", "income"),
    ("4200", "Cost-recovery income", "income"),
    ("5100", "Committee supplies expense", "expense"),
    ("5300", "Maintenance & repair expense", "expense"),
    ("5900", "Inventory shrinkage / adjustment", "expense"),
]


def seed_chart_of_accounts():
    """Create any missing chart-of-accounts rows. Idempotent (by ``code``).

    Returns a summary dict ``{"created", "existing", "total"}``.
    """
    created = 0
    existing = 0
    for code, name, type_word in CHART_OF_ACCOUNTS:
        account_type = AccountType[type_word]
        _, was_created = Account.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "type": account_type.value,
                "currencies": ["USD"],
            },
        )
        if was_created:
            created += 1
        else:
            existing += 1
    return {"created": created, "existing": existing, "total": len(CHART_OF_ACCOUNTS)}
