"""A payload that withholds vendor data and nobody classified fails the build.

WHY THIS EXISTS. The withheld shape has now been missed on FIVE separate
frontend surfaces — the item page's Unit Cost row, the item list's Unit Cost
column, the CSV export, the metrics strip and the kit list — each of which read
a key the server had stopped sending and rendered "nothing on file" for it. The
common cause was not carelessness on any one screen: nothing anywhere connected
"this serializer withholds" to "this TypeScript type must say so", so every
reader had to rediscover the third state for itself.

So the emitters are enumerated HERE, and each is mapped to the TypeScript type
it feeds. Adding a gated payload fails this test until somebody says which type
the web reads it through — and the failure message names that type, so the
person adding it is told what to go and change.

Same shape as ``inventory/tests/test_pack_size_single_owner.py`` and
``test_price_single_owner.py``: the set is DERIVED (the mixin's subclasses are
found by walking the class hierarchy, not listed), and only what cannot be
derived is registered by hand.

EVERY APP'S SERIALIZERS ARE IMPORTED FIRST, and that is load-bearing rather
than tidy. ``__subclasses__()`` reports only what Python has already imported,
and ``django.setup()`` imports app configs and models — not serializer modules.
Deriving the set after importing one app would have made this gate answer
"nothing new" for a mixin subclass landing in any OTHER app, and made the same
code pass or fail depending on what an earlier test in the run happened to
import.

THE HONEST LIMIT, named the way those two modules name theirs: this runs in
Python, so it can prove the BACKEND list is complete and it cannot prove the
TypeScript side was updated. What it can do is refuse to let a new emitter land
unnamed, and tell whoever added it which file to open. ``frontend`` type
declarations are checked by ``tsc`` and by each surface's own tests.
"""

from __future__ import annotations

import importlib
import pathlib

from django.apps import apps

import pytest

from inventory.services.vendor_visibility import VENDOR_WITHHELD_KEY, VendorGatedSerializerMixin

#: The repository's ``backend/`` root, which is what makes an app FIRST-PARTY.
#: Resolved on both sides of every comparison below, because a symlinked
#: checkout or a bind mount spells the same directory two ways.
BACKEND = pathlib.Path(__file__).resolve().parents[2]

#: Every serializer that withholds through the mixin, mapped to the TypeScript
#: type the web reads that payload through. The KEY set is derived below; this
#: dict supplies the answer a human has to give for each entry.
MIXIN_TS_TYPES: dict[str, str] = {
    "InventoryItemSerializer": "InventoryItem (frontend/src/types/index.ts)",
    "InventoryItemDetailSerializer": "InventoryItem (frontend/src/types/index.ts)",
    "KitSerializer": "Kit (frontend/src/types/index.ts)",
    "KitSummarySerializer": "KitSummary (frontend/src/types/index.ts)",
    "InventoryMetricsSerializer": "InventoryItemMetrics (frontend/src/types/index.ts)",
    "UsageLogSerializer": "UsageLog (frontend/src/types/index.ts)",
}

#: Emitters that set :data:`VENDOR_WITHHELD_KEY` BY HAND rather than through the
#: mixin, so no class walk can find them. Registered explicitly, with the same
#: obligation: each names the TypeScript type it feeds.
#:
#: They are hand-rolled for reasons, not by oversight: ``ResolvedScan`` is a
#: plain dataclass with no serializer, and the transparency action assembles
#: three arrays plus a summary as dicts.
HAND_ROLLED_EMITTERS: dict[str, str] = {
    "scanner.resolvers.ResolvedScan.to_dict": ("ScanDispatchResult (frontend/src/services/api.ts)"),
    "reorder_queue.views.AnalyticsViewSet.transparency:orders": (
        "TransparencyOrder (frontend/src/pages/TransparencyPage.tsx)"
    ),
    "reorder_queue.views.AnalyticsViewSet.transparency:ledger": (
        "LedgerEntry (frontend/src/pages/TransparencyPage.tsx)"
    ),
    "reorder_queue.views.AnalyticsViewSet.transparency:purchase_orders": (
        "TransparencyPurchaseOrder (frontend/src/pages/TransparencyPage.tsx)"
    ),
    "reorder_queue.views.AnalyticsViewSet.transparency:summary": (
        "TransparencySummary (frontend/src/pages/TransparencyPage.tsx)"
    ),
}


def _import_every_serializer_module() -> set[str]:
    """Import ``<app>.serializers`` for every installed app, and say which.

    ``__subclasses__()`` below can only see classes Python has imported, so the
    walk is exactly as wide as this is. An app with no such module is normal and
    ignored; an app whose module raises on import is NOT ignored, because a
    gated serializer could be hiding behind that error.

    THE RETURN VALUE IS THE WITNESS. Asking ``sys.modules`` afterwards proves
    nothing about this call: importing ``config.urls`` pulls in every app's
    serializers through the router, so the answer is already yes before the
    walk narrows to anything at all. Only what this call itself reached can
    show how wide it reached.
    """
    reached: set[str] = set()
    for config in apps.get_app_configs():
        name = f"{config.name}.serializers"
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
            continue
        reached.add(name)
    return reached


def _mixin_subclasses() -> tuple[dict[str, str], set[str]]:
    """Every ``VendorGatedSerializerMixin`` subclass, mapped to its module.

    Returns it with the set of serializer modules the walk was widened over, so
    a caller can check the widening rather than assume it.
    """
    reached = _import_every_serializer_module()

    found: dict[str, str] = {}

    def walk(cls) -> None:
        for sub in cls.__subclasses__():
            found[sub.__name__] = sub.__module__
            walk(sub)

    walk(VendorGatedSerializerMixin)
    return found, reached


@pytest.mark.unit
def test_every_gated_serializer_names_the_typescript_type_it_feeds():
    declared, _reached = _mixin_subclasses()

    # A DISAPPEARANCE has to fail too, so the guard names what it expects rather
    # than counting. "Non-empty" would have been satisfied by the inventory
    # classes no matter how much of the walk had broken.
    missing = sorted(set(MIXIN_TS_TYPES) - set(declared))
    assert not missing, (
        f"{missing} no longer reach(es) the walk. Either the serializer stopped "
        "gating its vendor keys — in which case say so and drop the entry — or "
        "the walk is broken (a moved module, an app's serializers no longer "
        "importable), and this gate is passing vacuously."
    )

    unclassified = sorted(set(declared) - set(MIXIN_TS_TYPES))
    assert not unclassified, (
        f"{unclassified} withhold(s) vendor keys and nothing says which "
        "TypeScript type reads the payload. A reader that does not know the "
        "keys are OPTIONAL renders '—' for them, which claims the ITEM has "
        "nothing on file when the truth is about the READER — the defect this "
        "gate exists to stop repeating. Add the serializer to MIXIN_TS_TYPES "
        "here naming its TS type, declare that type's vendor keys optional plus "
        "`vendor_data_withheld?: boolean`, and have the surface ask "
        "`utils/vendorVisibility` before rendering them."
    )


@pytest.mark.unit
def test_the_walk_reaches_serializers_outside_inventory():
    """The gate is only as wide as its imports, so prove the imports are wide.

    Every mixin subclass lives in ``inventory.serializers`` today, so a walk
    that had silently narrowed to that one module would look identical from the
    outside. Two things have to be true, and both were once asserted by a check
    that could not fail:

    * the widening runs THROUGH :func:`_mixin_subclasses`, which is what the
      gate above calls — asking the import helper directly proved the helper
      worked, not that anything used it;
    * the answer comes from what THIS CALL reached, not from ``sys.modules``,
      which by the time any test runs already holds every serializer module the
      URL conf imported — and holds ``rest_framework.serializers`` regardless.
      Both spellings passed with the walk narrowed all the way back down.
    """
    _declared, reached = _mixin_subclasses()

    first_party = {
        f"{config.name}.serializers"
        for config in apps.get_app_configs()
        if BACKEND in pathlib.Path(config.path).resolve().parents
    }
    outside = sorted(name for name in reached & first_party if not name.startswith("inventory."))
    assert outside, (
        "The walk widened over no first-party serializers module outside "
        f"inventory (it reached {sorted(reached)}), so a gated serializer "
        "landing in any other app would be invisible to this gate."
    )


@pytest.mark.unit
def test_the_hand_rolled_emitters_are_registered_with_their_types():
    """The emitters no class walk can see, named so they are not forgotten."""
    assert HAND_ROLLED_EMITTERS, "the hand-rolled register is empty"
    for emitter, ts_type in HAND_ROLLED_EMITTERS.items():
        assert ts_type.strip(), f"{emitter} names no TypeScript type"


@pytest.mark.integration
def test_a_withheld_scan_says_so_rather_than_going_quiet(db):
    """REGRESSION: ``ResolvedScan`` popped its vendor keys without the marker.

    A consumer then could not tell "withheld from you" from "this UPC matched no
    supplier link" — and the second is an ordinary outcome of an unknown
    barcode, so the two need different words on screen.
    """
    from inventory.models import ItemSupplier, Supplier
    from inventory.tests.factories import InventoryItemFactory
    from scanner.resolvers import resolve

    item = InventoryItemFactory(image=None)
    supplier = Supplier.objects.create(name="ZZQQ Marker Vendor", supplier_type="online")
    ItemSupplier.objects.create(item=item, supplier=supplier, unit_upc="012345678905")

    resolved = resolve("012345678905")
    assert resolved.supplier_name == "ZZQQ Marker Vendor"

    withheld = resolved.to_dict(include_vendor_data=False)
    assert withheld[VENDOR_WITHHELD_KEY] is True
    assert "supplier_name" not in withheld
    assert "item_supplier_id" not in withheld
    # What the scan is FOR survives, which is what keeps the anonymous flow
    # working: which item, what action, current stock.
    assert withheld["item_id"] == str(item.id)

    shown = resolved.to_dict(include_vendor_data=True)
    assert VENDOR_WITHHELD_KEY not in shown
    assert shown["supplier_name"] == "ZZQQ Marker Vendor"
