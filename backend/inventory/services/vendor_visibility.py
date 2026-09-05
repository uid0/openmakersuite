"""The ONE answer to "may this caller see vendor identity or vendor money?".

    "Vendor names should not be public, same with Vendor Pricing. They should
    always be behind user auth."  — the captain, op-anonymous-read-posture

That sentence covers vendor names, supplier part numbers, supplier UPCs, lead
times, and every form of vendor money — unit cost, case cost, price history,
invoices and agreements — on every surface: the web app, the REST API, document
and media downloads, exports, and the public transparency page.

WHY A MODULE AND NOT ``request.user.is_authenticated`` AT EACH SITE. This is the
same discipline as :mod:`inventory.services.supplier_selection` and
:mod:`inventory.services.pack_size`: one interpretation of one question, so the
next surface asks rather than re-derives. Three copies of a supplier ordering
rule had already drifted apart before op-2rsp collapsed them; an access rule
spelled out at twenty call sites drifts the same way, except that the drift is
a disclosure rather than a wrong number.

WHAT IS DELIBERATELY NOT HERE. This does not decide WHICH fields are vendor
facts — that belongs to the serializer that owns each field, and is recorded
there. It decides only WHO is asking. Keeping the two apart is what lets a
payload gate some keys and keep others (``InventoryItemSerializer`` keeps the
whole item identity and drops the vendor block) without a second predicate.

FAILS CLOSED, and that is load-bearing. A serializer built by hand without
``context``, a management command, a shell, a nested render somebody wired up
without forwarding context — none of them has proven anybody is signed in, so
none of them gets vendor data. The precedent is
``SupplierChoiceSerializer._serves_operator_detail``, which this generalises;
that method now delegates here rather than keeping its own copy of the rule.
"""

from __future__ import annotations

__all__ = [
    "VENDOR_WITHHELD_KEY",
    "VendorGatedSerializerMixin",
    "may_see_vendor_data",
    "vendor_visibility_from_context",
]


def may_see_vendor_data(request) -> bool:
    """Whether ``request`` may be shown vendor identity or vendor money.

    ``request`` may be ``None`` or any object without a ``user`` — both answer
    ``False``. Membership is not consulted: the captain's line is "behind user
    auth", so any authenticated account qualifies and an anonymous caller never
    does.
    """
    user = getattr(request, "user", None)
    return bool(user is not None and getattr(user, "is_authenticated", False))


def vendor_visibility_from_context(context) -> bool:
    """:func:`may_see_vendor_data` for a serializer's ``context`` dict.

    Serializers reach the request through ``self.context["request"]``, and a
    missing key must not raise — it must restrict. Spelled out here so no
    serializer has to remember which of the two failure shapes it is in.
    """
    return may_see_vendor_data((context or {}).get("request"))


#: Key added to a payload whose vendor block was withheld, so a consumer can
#: tell POLICY from ABSENCE.
#:
#: The keys themselves are OMITTED rather than nulled, and that choice is not
#: cosmetic: ``null`` already means "no price on file" / "no supplier on this
#: item" throughout this payload family (op-9m2v), so nulling would tell a
#: reader something false about the item instead of something true about the
#: reader — and a consumer's ``?? 0`` would render a withheld price as a real
#: $0.00, the exact falsy-zero class this codebase spent a week closing. An
#: absent key cannot be summed or formatted by accident. Same reasoning, and
#: same shape, as ``dashboard.views.get_inventory_summary``'s
#: ``total_value_withheld``.
VENDOR_WITHHELD_KEY = "vendor_data_withheld"


class VendorGatedSerializerMixin:
    """Drops :attr:`VENDOR_ONLY_FIELDS` from a payload served to a caller with
    no session, and says so with :data:`VENDOR_WITHHELD_KEY`.

    Mix into any serializer whose endpoint must stay publicly reachable while
    its vendor block must not be. A serializer whose endpoint is closed outright
    (``SupplierSerializer``, ``ItemSupplierSerializer``, ``PriceHistorySerializer``)
    does not need this and deliberately does not use it — a second gate behind a
    closed door is a place for the two to disagree.

    WHICH KEYS ARE VENDOR FACTS IS THE SERIALIZER'S OWN ANSWER, declared on the
    subclass. This mixin only asks :func:`may_see_vendor_data` who is reading.
    """

    #: Keys withheld from an unauthenticated caller. Declared per serializer.
    VENDOR_ONLY_FIELDS: tuple[str, ...] = ()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if vendor_visibility_from_context(self.context):
            return data
        for field in self.VENDOR_ONLY_FIELDS:
            data.pop(field, None)
        # Reported whenever the gate ran, not only when a value happened to be
        # present: "this item has no supplier" and "we are not telling you" are
        # different facts, and a surface that says the first when it means the
        # second is the defect this key exists to prevent.
        data[VENDOR_WITHHELD_KEY] = True
        return data
