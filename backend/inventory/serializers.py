"""
Serializers for inventory API.
"""

import copy
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

# The asset's breaker/disconnect FKs live on facilities.AssetSiteRequirements
# (#880); the serializer keeps the historical PK-shaped keys, so it needs the
# related querysets here. Safe at module top: serializers import only after all
# app models are loaded, so there is no import cycle.
from electrical_circuits.models import Disconnect, PowerBreaker
from membership.actor import actor_display

from .models import (
    Asset,
    AssetDocument,
    AssetMeter,
    AssetMeterReading,
    AssetOutOfService,
    AssetPart,
    AssetProblem,
    AssetProblemPhoto,
    AssetReservation,
    Category,
    ComponentUsageEvent,
    DemandForecast,
    Fixture,
    FixtureRefillRequest,
    InventoryItem,
    InventorySafetyProfile,
    ItemSupplier,
    KitComponent,
    Location,
    LocationProblem,
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceMaterial,
    MaintenanceRecord,
    MaintenanceTask,
    MaintenanceTool,
    PackagingLevel,
    PriceHistory,
    SerializedComponent,
    StockReconciliation,
    Supplier,
    SupplierAgreement,
    UsageLog,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderLotoCompletion,
    WorkOrderMaterialUsage,
    WorkOrderPhoto,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
    WorkOrderTool,
    WorkOrderValidation,
)


class SupplierSerializer(serializers.ModelSerializer):
    """Basic serializer for supplier list views."""

    item_count = serializers.SerializerMethodField()
    purchase_order_count = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()

    class Meta:
        model = Supplier
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at"]

    def get_item_count(self, obj):
        """Count of items supplied by this supplier."""
        return obj.supplier_items.filter(is_active=True).count()

    def get_purchase_order_count(self, obj):
        """Count of purchase orders with this supplier."""
        try:
            from reorder_queue.models import PurchaseOrder

            return PurchaseOrder.objects.filter(supplier=obj).count()
        except ImportError:
            return 0

    def get_total_spent(self, obj):
        """Sum of actual totals from received purchase orders."""
        try:
            from decimal import Decimal

            from django.db.models import Sum

            from reorder_queue.models import PurchaseOrder

            result = PurchaseOrder.objects.filter(
                supplier=obj, status=PurchaseOrder.Status.RECEIVED, actual_total__isnull=False
            ).aggregate(total=Sum("actual_total"))["total"] or Decimal("0.00")
            return str(result)
        except (ImportError, TypeError):
            return "0.00"


class SupplierAgreementSerializer(serializers.ModelSerializer):
    """Purchase/pricing agreement held with a supplier (op-yoos)."""

    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = SupplierAgreement
        fields = [
            "id",
            "supplier",
            "supplier_name",
            "name",
            "notes",
            "document",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class CategorySerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()
    parent_name = serializers.CharField(source="parent.name", read_only=True)
    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = "__all__"
        read_only_fields = ["slug"]

    def get_item_count(self, obj):
        """Count of items in this category."""
        return obj.items.filter(is_active=True).count()

    def get_children(self, obj):
        """Get child categories."""
        children = obj.children.all()
        return CategorySerializer(children, many=True).data


class LocationSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)
    fixture_count = serializers.SerializerMethodField()
    qr_code_url = serializers.SerializerMethodField()

    class Meta:
        model = Location
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at", "access_code"]

    def get_fixture_count(self, obj):
        """Count of fixtures at this location."""
        return obj.fixtures.filter(is_active=True).count()

    def get_qr_code_url(self, obj):
        """Get QR code URL if available."""
        if obj.qr_code:
            return obj.qr_code.url
        return None


class UsageLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = UsageLog
        fields = "__all__"
        # The committee-chargeback fields are populated server-side by the
        # ``log_usage`` action (which snapshots cost and posts the ledger entry);
        # they are read-only here so the generic usage-log CRUD endpoint cannot
        # set them out of band and leave the ledger inconsistent.
        read_only_fields = [
            "usage_date",
            "charged_group",
            "unit_cost",
            "total_cost",
            "charged_by",
            "ledger_transaction",
        ]


class PriceHistorySerializer(serializers.ModelSerializer):
    """Serializer for price history records."""

    item_name = serializers.CharField(source="item_supplier.item.name", read_only=True)
    supplier_name = serializers.CharField(source="item_supplier.supplier.name", read_only=True)
    price_change_percentage = serializers.DecimalField(
        max_digits=6, decimal_places=2, read_only=True
    )

    class Meta:
        model = PriceHistory
        fields = [
            "id",
            "item_name",
            "supplier_name",
            "unit_cost",
            "package_cost",
            "quantity_per_package",
            "change_type",
            "recorded_at",
            "notes",
            "price_change_percentage",
        ]
        read_only_fields = ["recorded_at", "price_change_percentage"]


class ItemSupplierSerializer(serializers.ModelSerializer):
    """Serializer for item-supplier relationships with pricing and dimensional data."""

    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    # REMOVED: recent_price_history to prevent circular recursion
    # Use ItemSupplierDetailSerializer for full details including price history

    # Calculated dimensional properties
    package_volume = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    unit_weight = serializers.DecimalField(max_digits=8, decimal_places=3, read_only=True)
    package_dimensions_display = serializers.CharField(read_only=True)

    class Meta:
        model = ItemSupplier
        fields = [
            "id",
            "item",
            "item_name",
            "supplier",
            "supplier_name",
            "supplier_sku",
            "supplier_url",
            "package_upc",
            "unit_upc",
            "quantity_per_package",
            # Dimensional fields
            "package_height",
            "package_width",
            "package_length",
            "package_weight",
            # Calculated dimensional properties
            "package_volume",
            "unit_weight",
            "package_dimensions_display",
            # Pricing
            "unit_cost",
            "package_cost",
            "average_lead_time",
            "is_primary",
            "is_active",
            "is_discontinued",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class ItemSupplierDetailSerializer(ItemSupplierSerializer):
    """
    Extended serializer with price history.
    Use this for detail views where full supplier information is needed.
    """

    recent_price_history = PriceHistorySerializer(source="price_history", many=True, read_only=True)

    class Meta(ItemSupplierSerializer.Meta):
        fields = ItemSupplierSerializer.Meta.fields + ["recent_price_history"]

    def to_representation(self, instance):
        """Limit price history to recent records for performance."""
        data = super().to_representation(instance)
        # Limit to most recent 10 price history records
        if "recent_price_history" in data:
            data["recent_price_history"] = data["recent_price_history"][:10]
        return data


class SupplierDetailSerializer(SupplierSerializer):
    """Extended serializer for supplier detail views with related data."""

    items = ItemSupplierSerializer(source="supplier_items", many=True, read_only=True)
    purchase_orders = serializers.SerializerMethodField()
    lead_time_analytics = serializers.SerializerMethodField()
    price_trends = serializers.SerializerMethodField()

    class Meta(SupplierSerializer.Meta):
        fields = [
            "id",
            "name",
            "supplier_type",
            "website",
            "account_number",
            "tax_free_paperwork_filed",
            "notes",
            "created_at",
            "updated_at",
            "item_count",
            "purchase_order_count",
            "total_spent",
            "items",
            "purchase_orders",
            "lead_time_analytics",
            "price_trends",
        ]

    def get_purchase_orders(self, obj):
        """Get purchase orders for this supplier."""
        try:
            from reorder_queue.models import PurchaseOrder
            from reorder_queue.serializers import PurchaseOrderSerializer

            # select_related feeds PurchaseOrderSerializer's
            # supplier_agreement_details (op-yoos) and its work-order /
            # committee identity blocks (op-shb9) without a query per order.
            orders = (
                PurchaseOrder.objects.filter(supplier=obj)
                .select_related(
                    "supplier_agreement",
                    "work_order",
                    "work_order__maintenance_item",
                    "work_order__asset",
                    "owning_group",
                )
                .prefetch_related("work_order__asset_problems", "items__owning_group")
                .order_by("-order_date")[:50]
            )
            return PurchaseOrderSerializer(orders, many=True, context=self.context).data
        except ImportError:
            return []

    def get_lead_time_analytics(self, obj):
        """Get lead time analytics for this supplier.

        Every rate and variance here is measured against the supplier link's
        standing quoted lead time, never against ``expected_delivery_date`` (see
        ``LeadTimeLog``). That used to reach this payload — and
        ``frontend/src/pages/SupplierDetailPage.tsx``'s ``LeadTimeChart`` —
        unnamed, so a vendor that quotes 3, has the order confirmed at 10 and
        delivers on day 10 arrived as ``expected_delivery_date ==
        actual_delivery_date`` alongside ``variance_days: 7, was_late: true``
        and read as a contradiction.

        So the KEYS name the yardstick too, not only the labels a person reads:
        ``within_quoted_lead_time_pct``, ``avg_variance_vs_quoted_lead_time_days``
        and each row's ``was_over_quoted_lead_time`` replace the bare
        ``on_time_percentage`` / ``average_variance`` / ``was_late``. This block
        has no external consumer — ScanTTY never calls the supplier endpoints and
        decodes none of these names — and this repo's web moves with it, so the
        rename is safe here in a way it is NOT for the reorders analytics
        payloads, whose keys ScanTTY decodes by name and which keep theirs.

        Alongside them, keys derived from ``LeadTimeLog`` so this payload cannot
        name a different promise than the admin does:
        ``variance_measured_against`` states the yardstick once for the whole
        block, and each row's ``met_confirmed_date`` plus
        ``confirmed_delivery_date`` carry the other promise — the verdict and the
        date an operator would chase the vendor with, both ``null`` when no date
        was confirmed on the order. Pinned by
        ``test_lead_time_yardstick_is_named.py``.
        """
        try:
            from django.db.models import Avg, Count, Max, Min

            from reorder_queue.models import LeadTimeLog

            # Get all lead time logs for items from this supplier
            logs = LeadTimeLog.objects.filter(item_supplier__supplier=obj)

            if not logs.exists():
                return {
                    "average_lead_time": None,
                    "min_lead_time": None,
                    "max_lead_time": None,
                    "avg_variance_vs_quoted_lead_time_days": None,
                    "total_orders": 0,
                    "within_quoted_lead_time_pct": None,
                    # Present on the empty block too, so a consumer can read the
                    # yardstick without it appearing and vanishing with the data.
                    "variance_measured_against": LeadTimeLog.VARIANCE_YARDSTICK,
                }

            stats = logs.aggregate(
                avg_lead_time=Avg("actual_lead_time_days"),
                min_lead_time=Min("actual_lead_time_days"),
                max_lead_time=Max("actual_lead_time_days"),
                avg_variance=Avg("variance_days"),
                total_orders=Count("id"),
            )

            # Calculate on-time percentage
            on_time_count = logs.filter(variance_days__lte=0).count()
            on_time_percentage = (
                (on_time_count / stats["total_orders"] * 100) if stats["total_orders"] > 0 else None
            )

            return {
                # ``is not None``, never truthiness: a vendor that landed every
                # order exactly on its quote averages a variance of 0.0, and the
                # falsy spelling served that perfect record as ``null`` — the
                # card beside it reading "100.0% within quoted lead time" while
                # this one read "N/A". A same-day supplier's 0-day average lead
                # time is the same fact. Both are answers, not absences; these
                # are ``None`` only with no rows at all, which returns above.
                "average_lead_time": (
                    float(stats["avg_lead_time"]) if stats["avg_lead_time"] is not None else None
                ),
                "min_lead_time": stats["min_lead_time"],
                "max_lead_time": stats["max_lead_time"],
                "avg_variance_vs_quoted_lead_time_days": (
                    float(stats["avg_variance"]) if stats["avg_variance"] is not None else None
                ),
                "total_orders": stats["total_orders"],
                "within_quoted_lead_time_pct": (
                    float(on_time_percentage) if on_time_percentage is not None else None
                ),
                # What every variance and rate above and below is measured
                # against, said once for the whole block instead of repeated on
                # each row: it is the same for all of them.
                "variance_measured_against": LeadTimeLog.VARIANCE_YARDSTICK,
                "recent_logs": [
                    {
                        "item_name": log.item_supplier.item.name,
                        "order_date": log.order_date.isoformat(),
                        "expected_delivery_date": log.expected_delivery_date.isoformat(),
                        "actual_delivery_date": log.actual_delivery_date.isoformat(),
                        "estimated_lead_time_days": log.estimated_lead_time_days,
                        "actual_lead_time_days": log.actual_lead_time_days,
                        "variance_days": log.variance_days,
                        "was_over_quoted_lead_time": log.was_late,
                        # The other promise, which varies per row and which
                        # nothing here scores: the verdict AND the date it was
                        # judged against, both ``null`` where the order carries
                        # no confirmed date. Never the row's own
                        # ``expected_delivery_date`` — that falls back to the
                        # quote and is then a date nobody agreed to.
                        "met_confirmed_date": log.met_confirmed_date,
                        "confirmed_delivery_date": (
                            log.confirmed_delivery_date.isoformat()
                            if log.confirmed_delivery_date is not None
                            else None
                        ),
                    }
                    for log in logs.order_by("-actual_delivery_date").select_related(
                        "purchase_order", "item_supplier__item"
                    )[:10]
                ],
            }
        except ImportError:
            return {}

    def get_price_trends(self, obj):
        """Get price trends for items from this supplier."""
        try:
            from datetime import timedelta

            from django.utils import timezone

            from inventory.services.pricing import (
                package_price_of,
                price_float,
                unit_price_of,
            )

            # Get price history for items from this supplier
            price_history = PriceHistory.objects.filter(item_supplier__supplier=obj).order_by(
                "-recorded_at"
            )

            if not price_history.exists():
                return {
                    "trends": [],
                    "summary": {
                        "average_unit_cost": None,
                        "min_unit_cost": None,
                        "max_unit_cost": None,
                        "price_changes_count": 0,
                    },
                }

            # Get recent price changes (last 6 months)
            six_months_ago = timezone.now() - timedelta(days=180)
            recent_history = price_history.filter(recorded_at__gte=six_months_ago)

            # Group by item and get trends
            trends = []
            items_seen = set()

            for price_record in recent_history[:50]:  # Limit to 50 most recent
                item_supplier = price_record.item_supplier
                item_id = str(item_supplier.item.id)

                if item_id not in items_seen:
                    items_seen.add(item_id)
                    # Get all price history for this item-supplier
                    item_history = PriceHistory.objects.filter(
                        item_supplier=item_supplier
                    ).order_by("recorded_at")[:20]

                    trends.append(
                        {
                            "item_id": item_id,
                            "item_name": item_supplier.item.name,
                            "price_history": [
                                {
                                    "recorded_at": ph.recorded_at.isoformat(),
                                    # A snapshot recording 0.00 is a price the
                                    # supplier charged, and a 0% change is "no
                                    # change" rather than "no data" — neither
                                    # survives a truthiness guard (op-9m2v).
                                    "unit_cost": price_float(unit_price_of(ph)),
                                    "package_cost": price_float(package_price_of(ph)),
                                    "change_type": ph.change_type,
                                    "price_change_percentage": (
                                        None
                                        if ph.price_change_percentage is None
                                        else float(ph.price_change_percentage)
                                    ),
                                }
                                for ph in item_history
                            ],
                        }
                    )

            # Calculate summary statistics. Every snapshot that RECORDS a
            # price counts, ``0.00`` included — the summary is what this
            # supplier has charged. These three figures are UNCHANGED from
            # base, which already spelled this filter
            # ``if ph.unit_cost is not None``; only the per-record
            # ``unit_cost`` / ``package_cost`` inside ``trends`` above moved
            # (``null`` -> ``0.0`` for a recorded zero). Rewritten through the
            # derivation and pinned so the filter cannot quietly become a
            # truthiness one, which WOULD push the average and the minimum up
            # (op-9m2v).
            unit_costs = [
                float(price.amount)
                for price in (unit_price_of(ph) for ph in price_history)
                if price.is_known
            ]

            return {
                "trends": trends,
                "summary": {
                    "average_unit_cost": (
                        sum(unit_costs) / len(unit_costs) if unit_costs else None
                    ),
                    "min_unit_cost": min(unit_costs) if unit_costs else None,
                    "max_unit_cost": max(unit_costs) if unit_costs else None,
                    "price_changes_count": price_history.count(),
                },
            }
        except Exception:
            return {"trends": [], "summary": {}}


class PackagingLevelSerializer(serializers.ModelSerializer):
    """One rung of an item's packaging chain (op-hzji).

    Used nested-writable on :class:`InventoryItemSerializer` so the item form
    saves the whole chain in a single request; there is deliberately no
    standalone endpoint in phase 1.
    """

    per_parent = serializers.SerializerMethodField()

    class Meta:
        model = PackagingLevel
        fields = ["id", "name", "sort_order", "base_units", "per_parent"]

    def get_per_parent(self, obj):
        """How many of the next rung down fit in this one — the "case = 10 reams" number.

        ``None`` for the base rung, which has nothing below it. A chain is only
        required to shrink, not to divide evenly, so this is true division
        narrowed back to an ``int`` when it comes out whole — a case of 10 reams
        reads ``10``, not ``10.0``, while an uneven 10-of-4 rung reads ``2.5``
        instead of silently rounding to ``2``.

        Reads the item's levels through the relation so a caller that prefetched
        ``packaging_levels`` pays no per-row query.
        """
        if obj.pk is None:
            return None
        siblings = sorted(obj.item.packaging_levels.all(), key=lambda level: level.sort_order)
        smaller = [level for level in siblings if level.sort_order > obj.sort_order]
        if not smaller or smaller[0].base_units < 1:
            return None
        ratio = obj.base_units / smaller[0].base_units
        return int(ratio) if ratio.is_integer() else ratio


class SupplierChoiceAlternativeSerializer(serializers.Serializer):
    """One supplier that was on offer and did not win.

    Deliberately just the identity: a surface uses this to say "and two others",
    or to name them, not to quote a second price. The full row for any of them
    is in ``suppliers[]`` under the same ``id``.
    """

    id = serializers.IntegerField()
    supplier_name = serializers.CharField()


class SupplierChoiceSerializer(serializers.Serializer):
    """Which supplier we would buy this item from, and why that one (op-3xsp).

    The wire form of
    :class:`~inventory.services.supplier_selection.SupplierChoice`, and the
    field every surface that NAMES a supplier is meant to read. The flat
    ``supplier_name`` beside it is the same winner with the derivation thrown
    away, which is what made "this item has three suppliers" render as "this
    item's supplier is Acme" on a scan screen, a reorder queue and an exported
    CSV somebody then ordered from.

    The three honesty fields are the point of the object, not decoration:

    * ``alternatives`` — everything else that could have been bought from. A
      surface with room says "Acme, or 2 others"; one without at least stops
      implying there was nothing else.
    * ``scored_without_price`` / ``scored_without_history`` — the scoring
      punishes NEITHER gap (see the service), so the winner can have won while
      nobody knew its price or whether it has ever delivered. An operator
      reading a blank cost cell cannot tell that from "no supplier at all"
      unless it is said.
    * ``flagged_primary_unorderable`` — the operator flagged one and it was
      skipped as unbuyable, which reads to them as their choice being ignored.

    ``reason`` is set (and ``supplier_name`` null) exactly when there is nothing
    to buy from: ``no_suppliers`` versus ``none_orderable``, which need
    different words and different actions from an operator.

    ADDITIVE on the wire: nothing here replaces or renames an existing key, so
    a client that has never heard of it — ScanTTY decodes item JSON into a
    struct without ``DisallowUnknownFields`` — is unaffected.

    NOT THE SAME OBJECT FOR EVERYONE. ``InventoryItemViewSet`` declares no
    ``permission_classes`` at all — its ``get_permissions`` returns
    ``AllowAny`` for the public actions (``list``, ``retrieve``, ``metrics``,
    ``low_stock`` and the rest of that list) — and ``KitViewSet`` sets
    ``permission_classes = [IsAuthenticatedOrReadOnly]``. So either one serves
    a read to an anonymous caller: this renders for logged-out callers too, and
    the four keys in
    :attr:`OPERATOR_ONLY_FIELDS` are OMITTED for them — see that attribute for
    which and why. Omitted, not nulled: a null ``scored_without_price`` is a
    claim about the item, an absent one is a statement about the reader.
    """

    # Declared for the shape only — drf-spectacular reads these to build the
    # OpenAPI schema, and ``to_representation`` below is what actually runs.
    # The reading is not automatic: ``supplier_choice`` is a
    # ``SerializerMethodField``, which spectacular types as an untyped object
    # unless the getter carries ``@extend_schema_field(SupplierChoiceSerializer)``
    # — so that decorator is what makes the sentence above true, and
    # ``config/tests/test_schema.py`` asserts the generated document says so.
    # No ``source="item_supplier.supplier.name"`` here, because DRF's dotted
    # source RAISES rather than yielding ``null`` when an intermediate is
    # ``None`` — and ``item_supplier`` is ``None`` for exactly the case this
    # object exists to describe.
    item_supplier_id = serializers.IntegerField(allow_null=True)
    supplier_name = serializers.CharField(allow_null=True)
    reason = serializers.CharField(allow_null=True)
    alternatives = SupplierChoiceAlternativeSerializer(many=True)
    # ``required=False`` is not about writes — this serializer is read-only and
    # ``to_representation`` is fully overridden. It is what makes the published
    # schema tell the truth: these four keys are ABSENT from an unauthenticated
    # response, so a generated client must treat them as optional. A document
    # promising a field the server does not always send is the same defect this
    # object exists to close.
    basis = serializers.CharField(allow_null=True, required=False)
    flagged_primary_unorderable = serializers.BooleanField(required=False)
    scored_without_price = serializers.BooleanField(required=False)
    scored_without_history = serializers.BooleanField(required=False)

    #: Keys served only to a signed-in caller.
    #:
    #: These four describe HOW the derivation reached its answer, and they are
    #: addressed to whoever maintains the supplier links: "your flagged primary
    #: cannot be ordered from" means nothing to a member who has no flagged
    #: primary and no way to order. The web already withholds them from a
    #: logged-out visitor on every surface; without this they were still one
    #: network-tab glance away, which makes those gates a courtesy rather than a
    #: boundary.
    #:
    #: Deliberately NOT here: ``supplier_name``, ``item_supplier_id``,
    #: ``reason`` and ``alternatives``. Every supplier name in ``alternatives``
    #: is already in ``suppliers[]`` on this same payload, alongside its SKU,
    #: UPCs, cost and lead time, and that array predates this field. Hiding one
    #: while the other sits beside it would look like a protection and be none.
    OPERATOR_ONLY_FIELDS = (
        "basis",
        "flagged_primary_unorderable",
        "scored_without_price",
        "scored_without_history",
    )

    def _serves_operator_detail(self) -> bool:
        """Whether this render may carry the derivation metadata.

        FAILS CLOSED. A serializer with no ``request`` in context — a shell, a
        management command, a nested render somebody built by hand — has not
        proven anybody is authenticated, so it gets the restricted form.
        """
        user = getattr(self.context.get("request"), "user", None)
        return bool(user and user.is_authenticated)

    def to_representation(self, instance):
        """Flatten the choice, tolerating the no-supplier case (see above)."""
        link = instance.item_supplier
        data = {
            "item_supplier_id": link.id if link else None,
            "supplier_name": link.supplier.name if link else None,
            "basis": instance.basis,
            "reason": instance.reason,
            "flagged_primary_unorderable": instance.flagged_primary_unorderable,
            "scored_without_price": instance.scored_without_price,
            "scored_without_history": instance.scored_without_history,
            "alternatives": [
                {"id": other.id, "supplier_name": other.supplier.name}
                for other in instance.alternatives
            ],
        }
        if not self._serves_operator_detail():
            for field in self.OPERATOR_ONLY_FIELDS:
                data.pop(field)
        return data


class InventoryItemSerializer(serializers.ModelSerializer):
    # Primary-supplier compat fields (issue #882). ``supplier_name`` here and the
    # flat ``supplier_sku`` / ``supplier_url`` / ``unit_cost`` / ``package_cost``
    # / ``quantity_per_package`` / ``average_lead_time`` keys listed in
    # ``Meta.fields`` are READ-ONLY legacy accessors for the item's primary
    # supplier, superseded by the ``suppliers[]`` array (below), the
    # ``supplier_choice`` object (below) and the ``/metrics/``
    # (``?with_metrics=1``) endpoint. They are retained because ScanTTY's detail
    # screen reads all seven of them (``internal/tui/inventory_detail.go``) and
    # the web reads FOUR. Each reader below was confirmed by opening the call
    # site: a read only counts here when the object is an ``InventoryItem`` (or
    # ``Kit``) payload from THIS serializer. Reads off an ``ItemSupplier`` row
    # (``suppliers[]``, ``SupplierRelationshipForm``) are that row's own
    # columns, and the order pad's look-alike keys are built per
    # ``item_supplier`` in ``reorder_queue/views.py:by_supplier`` — neither is
    # this field.
    #
    #   * ``supplier_sku``      — the kit list's SKU cell and the "From" column
    #                             that attributes it (``KitListPage.tsx``), and
    #                             the kit form's SKU box (``KitDetailPage.tsx``,
    #                             ``applyKit``). ``KitSerializer`` subclasses
    #                             this one, so ``kit.supplier_sku`` IS the flat
    #                             accessor. Both are gated to signed-in viewers,
    #                             which changes who may see it, not who reads it;
    #   * ``supplier_url``      — the "View on <supplier>" link on the admin
    #                             dashboard's by-supplier order pad
    #                             (``AdminDashboard.tsx``, via
    #                             ``request.item_details``);
    #   * ``unit_cost``         — every price rendered as a number rather than
    #                             as a named supplier's price (op-9m2v): the
    #                             item detail card and its cost widget, the
    #                             inventory list and table, the kit list and kit
    #                             form, the scan page's cost row, the inventory
    #                             CSV export, and the work order material picker;
    #   * ``average_lead_time`` — the wait quoted beside a supplier the surface
    #                             has already named from ``supplier_choice``:
    #                             the scan page's info block and the admin
    #                             dashboard's Lead Time column.
    #
    # ``supplier_name`` had no web reader left after op-3xsp — the scan page,
    # the inventory CSV export, the reorder queue and the item page's anonymous
    # block all moved onto ``supplier_choice``. ``package_cost`` and
    # ``quantity_per_package`` have none either; the web reads those off
    # ``suppliers[]`` and the order pad. ScanTTY still reads all seven, so they
    # all stay. A future hard-removal needs coordinated ScanTTY + web changes.
    # They resolve through the prefetch-friendly
    # ``InventoryItem.primary_item_supplier`` so serialising a page no longer
    # costs a query per row.
    #
    # What they cannot say is the reason. A flat name is the winner of a
    # three-step derivation with the derivation thrown away: it cannot report
    # that four other suppliers were on offer, that the scoring picked this one
    # without knowing a price for it, or that the operator's own flagged primary
    # was skipped as unbuyable. Surfaces that NAME a supplier read
    # ``supplier_choice`` for exactly that reason (op-3xsp).
    supplier_name = serializers.SerializerMethodField()
    category_name = serializers.CharField(source="category.name", read_only=True)

    def get_supplier_name(self, obj):
        """Safely get supplier name, handling None values."""
        supplier = obj.supplier if hasattr(obj, "supplier") else None
        return supplier.name if supplier else None

    needs_reorder = serializers.BooleanField(read_only=True)
    # ``null`` when no supplier records a price for the item: the stock's value
    # is unknown, and base said ``"0.00"`` — a claim about money nobody made
    # (op-9m2v). Every consumer moved in the same commit: ``types/index.ts``
    # (``string | null``) and ScanTTY's already-nil-tolerant ``DecimalString``.
    total_value = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True, allow_null=True
    )
    image = serializers.ImageField(read_only=True)
    thumbnail = serializers.SerializerMethodField()
    qr_code_url = serializers.SerializerMethodField()
    location = serializers.CharField(source="location.name", read_only=True)

    # Complete supplier information array
    suppliers = ItemSupplierSerializer(source="item_suppliers", many=True, read_only=True)

    # Which supplier we would buy this item from, AND why that one (op-3xsp).
    # See :class:`SupplierChoiceSerializer`.
    #
    # THIS SERIALIZER NEEDS ``context``. The audience for the four operator-only
    # keys is read off ``context["request"].user`` and FAILS CLOSED, so anything
    # that builds this serializer — or any serializer that nests it, however
    # deep — by hand rather than through ``get_serializer()`` must pass
    # ``context=self.get_serializer_context()`` (a view) or ``context=self.context``
    # (a parent serializer's method field). Omitting it does not raise: it
    # quietly hands an authenticated operator the anonymous view, dropping the
    # very caveats this field exists to deliver. DRF supplies context to a
    # DECLARED nested field automatically; the hand-built call is the one to
    # watch.
    supplier_choice = serializers.SerializerMethodField()

    @extend_schema_field(SupplierChoiceSerializer)
    def get_supplier_choice(self, obj):
        """Serialise ``InventoryItem.supplier_choice``.

        Costs no query beyond the one the flat compat fields already pay: the
        choice is memoised on the instance, and rides the ``item_suppliers``
        prefetch every read path here sets up.

        ``context`` is forwarded because the nested serializer decides its own
        AUDIENCE from ``request.user`` — a hand-built instance carries none, and
        :meth:`SupplierChoiceSerializer._serves_operator_detail` fails closed,
        so dropping this argument silently restricts every caller.
        """
        return SupplierChoiceSerializer(obj.supplier_choice, context=self.context).data

    # Reorder status and tracking fields
    reorder_status = serializers.CharField(read_only=True)
    has_pending_reorder = serializers.BooleanField(read_only=True)
    expected_delivery_date = serializers.DateField(
        source="get_expected_delivery_date", read_only=True
    )
    active_reorder_request = serializers.SerializerMethodField()

    # Case-based reordering fields. ``current_cases`` is NULL when nothing
    # records how many units a case holds — see ``InventoryItem.current_cases``
    # (op-c1ke). Base sent the raw base-unit count there, so "10 cases" meant
    # ten loose units. Every consumer is null-aware: the three web pages that
    # render it show an em dash, and ScanTTY's ``CurrentCases`` was already a
    # nil-checked ``*float64``.
    current_cases = serializers.FloatField(read_only=True, allow_null=True)

    # Hazmat writable fields. These moved off InventoryItem onto the 1:1
    # InventorySafetyProfile (#885), so they are declared explicitly here (they
    # are no longer model fields). On read they resolve through the item's
    # compat properties (profile value, or the historical default); on write
    # they are popped and written through to the profile in create()/update().
    # Types/validators mirror the original model fields exactly.
    is_hazardous = serializers.BooleanField(required=False)
    msds_url = serializers.URLField(required=False, allow_blank=True, max_length=200)
    nfpa_health_hazard = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=4
    )
    nfpa_fire_hazard = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=4
    )
    nfpa_instability_hazard = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=4
    )
    nfpa_special_hazards = serializers.CharField(required=False, allow_blank=True, max_length=20)

    # Hazmat calculated fields
    nfpa_fire_diamond_display = serializers.ReadOnlyField()
    hazmat_compliance_status = serializers.ReadOnlyField()
    has_complete_nfpa_data = serializers.ReadOnlyField()
    msds_file_url = serializers.SerializerMethodField()

    # Cycle-count tracking (op-c7y4): whole days since the most recent cycle
    # count, or None if the item has never been counted.
    days_since_last_count = serializers.SerializerMethodField()

    # Unit of measure / packaging chain (op-hzji). ``packaging_levels`` is
    # nested-writable so the item form saves the whole chain in one request;
    # ``on_hand_display`` renders the canonical ``current_stock`` at the item's
    # counting granularity without changing it.
    packaging_levels = PackagingLevelSerializer(many=True, required=False)
    on_hand_display = serializers.SerializerMethodField()
    reorder_display = serializers.SerializerMethodField()
    # The write complement of ``on_hand_display`` for the manual stock-set path
    # (op-ev14): set on-hand as a count of whole ``count_level`` packs and let
    # the server convert. ``current_stock`` itself stays writable and stays base
    # units, so nothing about the existing edit form changes.
    current_stock_at_level = serializers.IntegerField(
        min_value=0,
        required=False,
        write_only=True,
        help_text="Set current_stock as a count of whole count_level packs "
        "(e.g. 3 cases). Converted to base units on save; only valid for an item "
        "counted in packs, and not alongside current_stock.",
    )

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "name",
            "description",
            "sku",
            "image",
            "thumbnail",
            "qr_code_url",
            "category",
            "category_name",
            "location",
            "reorder_quantity",
            "current_stock",
            "minimum_stock",
            # Case-based reordering fields
            "use_case_based_reorder",
            "minimum_cases",
            "reorder_cases",
            "current_cases",
            "reorder_instruction",
            # Unit of measure / packaging matrix (op-hzji). Additive and opt-in:
            # an item that sets none of these counts individual base units
            # exactly as it always has.
            "base_unit",
            "count_mode",
            "count_level",
            "open_container_count",
            "packaging_levels",
            "on_hand_display",
            "reorder_display",
            "current_stock_at_level",
            # ML demand-forecast opt-in (read+write; default OFF). The "ping me"
            # toggle that puts an item into the reorder_alerts notify set.
            "reorder_alerts_enabled",
            "supplier_name",
            "supplier_sku",
            "supplier_url",
            "unit_cost",
            "package_cost",
            "quantity_per_package",
            "average_lead_time",
            "qr_code",
            # Complete supplier array with all details
            "suppliers",
            # The winner out of that array, and why it won (op-3xsp).
            "supplier_choice",
            # Reorder status and tracking
            "reorder_status",
            "has_pending_reorder",
            "expected_delivery_date",
            "active_reorder_request",
            # Hazmat fields
            "is_hazardous",
            "msds_url",
            "msds_file_url",
            "nfpa_health_hazard",
            "nfpa_fire_hazard",
            "nfpa_instability_hazard",
            "nfpa_special_hazards",
            "nfpa_fire_diamond_display",
            "hazmat_compliance_status",
            "has_complete_nfpa_data",
            "is_active",
            # Retirement (op-jv7r). ``is_retired`` is writable so the item form
            # + admin can toggle phase-out directly; ``retired_at`` is a
            # read-only audit stamp set by the retire/unretire actions.
            "is_retired",
            "retired_at",
            "is_requestable",
            # Serialized-component tracking (#818). Writable so the item
            # create/edit form can flag an item as serialized and pick the
            # lifecycle tracking mode (op-5tc) — this is the switch that makes
            # the whole serialized-component feature reachable.
            "is_serialized",
            "serial_tracking_mode",
            "last_scanned_at",
            "last_counted_at",
            "days_since_last_count",
            "notes",
            "needs_reorder",
            "total_value",
            # Ownership fields
            "ownership_type",
            "owning_user",
            "owning_group",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "qr_code",
            "created_at",
            "updated_at",
            "last_counted_at",
            "retired_at",
        ]

    def get_thumbnail(self, obj):
        """Return the thumbnail URL when available."""
        try:
            if obj.thumbnail:
                request = self.context.get("request")
                if request:
                    return request.build_absolute_uri(obj.thumbnail.url)
                return obj.thumbnail.url
            return None
        except Exception:
            return None

    def get_qr_code_url(self, obj):
        """Return the QR code URL when available."""

        try:
            return obj.qr_code.url if obj.qr_code else None
        except Exception:
            return None

    def get_msds_file_url(self, obj):
        """Return the MSDS file URL when available."""
        try:
            return obj.msds_file.url if obj.msds_file else None
        except Exception:
            return None

    def get_days_since_last_count(self, obj):
        """Whole days since the most recent cycle count, or None if never counted."""
        if not obj.last_counted_at:
            return None
        from django.utils import timezone

        return (timezone.now() - obj.last_counted_at).days

    def get_on_hand_display(self, obj):
        """Current stock expressed at the item's counting granularity (op-hzji)."""
        from inventory.services.packaging import on_hand_display

        return on_hand_display(obj)

    def get_reorder_display(self, obj):
        """Reorder point + current count in one unit (op-es7c).

        Lets a client label the threshold correctly per mode ("reorder at 2
        cases") without re-deriving which of ``minimum_stock``/``minimum_cases``
        the item's ``count_mode`` gives meaning to. Reads only ``count_level``
        (already select_related on every queryset that serialises items) and
        ``item_suppliers`` (already prefetched), so it costs no extra query.
        """
        from inventory.services.packaging import reorder_display

        return reorder_display(obj)

    def get_active_reorder_request(self, obj):
        """Return details of the active reorder request if any."""
        active_request = obj.get_active_reorder_request()
        if active_request:
            return {
                "id": active_request.id,
                "status": active_request.status,
                "quantity": active_request.quantity,
                "requested_at": active_request.requested_at,
                "ordered_at": active_request.ordered_at,
                "requested_by": active_request.requested_by,
                "priority": active_request.priority,
                # Review/approval information
                "reviewed_by": (
                    active_request.reviewed_by.username if active_request.reviewed_by else None
                ),
                "reviewed_at": active_request.reviewed_at,
            }
        return None

    # ── Hazmat write-through to InventorySafetyProfile (#885) ────────────────
    # The flat hazmat keys are persisted on the item's 1:1 safety profile. Keep
    # the external contract identical: writes are create-on-first-write (a row
    # is only made when the payload carries non-default data) and
    # partial-update-safe (a PATCH that omits a field leaves it untouched).
    HAZMAT_WRITE_FIELDS = (
        "is_hazardous",
        "msds_url",
        "nfpa_health_hazard",
        "nfpa_fire_hazard",
        "nfpa_instability_hazard",
        "nfpa_special_hazards",
    )

    def _pop_hazmat(self, validated_data):
        return {
            field: validated_data.pop(field)
            for field in self.HAZMAT_WRITE_FIELDS
            if field in validated_data
        }

    def _apply_hazmat(self, instance, hazmat):
        if not hazmat:
            return
        profile = instance._get_safety_profile()
        if profile is None:
            profile = InventorySafetyProfile(item=instance)
        for field, value in hazmat.items():
            setattr(profile, field, value)
        # Persist when a row already exists (so un-setting a value sticks) or
        # when the incoming data is non-default (create-on-first-write).
        if profile.pk or profile.has_hazmat_data():
            profile.save()
            instance.safety_profile = profile

    # ── Packaging chain: nested write + cross-field validation (op-hzji) ──────
    def validate(self, attrs):
        """Reject packaging chains and count-mode/level pairs that cannot hold.

        ``PackagingLevel.clean()`` enforces the chain rules for a single-row
        save, but a nested write is a bulk write and never calls it — so the
        same shared validator runs here, plus the one check the model cannot
        make: a level named by ``count_level`` has to survive a chain
        replacement happening in the same request.

        Also resolves ``current_stock_at_level`` (op-ev14) into ``current_stock``
        once the count mode/level pair is known to hold.
        """
        attrs = super().validate(attrs)
        from inventory.services.packaging import (
            resolve_base_quantity,
            resolve_count_level_error,
            validate_packaging_chain,
        )

        levels = attrs.get("packaging_levels")
        if levels is not None:
            try:
                validate_packaging_chain(levels)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"packaging_levels": exc.messages})

        def effective(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None) if self.instance else None

        count_mode = effective("count_mode") or InventoryItem.CountMode.EACH
        error = resolve_count_level_error(
            count_mode,
            effective("count_level"),
            self.instance,
            {level["sort_order"] for level in levels} if levels is not None else None,
        )
        if error:
            raise serializers.ValidationError({"count_level": [error]})

        pack_count = attrs.pop("current_stock_at_level", None)
        if pack_count is not None:
            if "current_stock" in attrs:
                raise serializers.ValidationError(
                    {
                        "current_stock_at_level": [
                            "Send either current_stock (base units) or "
                            "current_stock_at_level (packs), not both."
                        ]
                    }
                )
            if self.instance is None:
                # A brand-new item has no packaging rung for count_level to
                # point at yet, so it cannot be counted in packs on create.
                raise serializers.ValidationError(
                    {
                        "current_stock_at_level": [
                            "Set the packaging chain and count level first, then "
                            "set stock in packs."
                        ]
                    }
                )
            # Convert against the mode/level the item will HAVE, so one request
            # can both opt an item into a pack mode and set its stock in packs.
            # A shallow copy keeps the shared conversion seam (rather than
            # re-deriving the arithmetic here) without mutating the instance DRF
            # is about to save.
            prospective = copy.copy(self.instance)
            prospective.count_mode = count_mode
            prospective.count_level = effective("count_level")
            try:
                attrs["current_stock"] = resolve_base_quantity(
                    prospective, pack_count, at_level=True
                )
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"current_stock_at_level": exc.messages})
        return attrs

    def _apply_packaging_levels(self, instance, levels):
        """Replace the item's chain with ``levels``, upserting on ``sort_order``.

        Upsert rather than delete-and-recreate so a rung that keeps its position
        keeps its primary key — otherwise every save would break the
        ``count_level`` foreign key pointing at it.
        """
        if levels is None:
            return
        with transaction.atomic():
            keep = set()
            for level in levels:
                row, _ = PackagingLevel.objects.update_or_create(
                    item=instance,
                    sort_order=level["sort_order"],
                    defaults={"name": level["name"], "base_units": level["base_units"]},
                )
                keep.add(row.pk)
            instance.packaging_levels.exclude(pk__in=keep).delete()

    def create(self, validated_data):
        hazmat = self._pop_hazmat(validated_data)
        levels = validated_data.pop("packaging_levels", None)
        instance = super().create(validated_data)
        self._apply_hazmat(instance, hazmat)
        self._apply_packaging_levels(instance, levels)
        return instance

    def update(self, instance, validated_data):
        hazmat = self._pop_hazmat(validated_data)
        levels = validated_data.pop("packaging_levels", None)
        instance = super().update(instance, validated_data)
        self._apply_hazmat(instance, hazmat)
        self._apply_packaging_levels(instance, levels)
        return instance


class InventoryItemDetailSerializer(InventoryItemSerializer):
    """Extended serializer with related data and full supplier details including price history."""

    recent_usage = UsageLogSerializer(source="usage_logs", many=True, read_only=True)
    supplier_details = SupplierSerializer(source="supplier", read_only=True)
    category_details = CategorySerializer(source="category", read_only=True)
    all_suppliers = ItemSupplierDetailSerializer(source="item_suppliers", many=True, read_only=True)
    price_trend_summary = serializers.SerializerMethodField()
    serialized_stock = serializers.SerializerMethodField()

    class Meta(InventoryItemSerializer.Meta):
        fields = InventoryItemSerializer.Meta.fields + [
            "recent_usage",
            "supplier_details",
            "category_details",
            "all_suppliers",
            "price_trend_summary",
            "serialized_stock",
        ]

    def get_serialized_stock(self, obj):
        """Serialized units summary for the item-detail serialized panel.

        Returns ``{"available", "on_hand", "installed"}`` for serialized items,
        ``None`` otherwise. ``on_hand`` is every physically-present (not-yet-
        depleted) unit; ``available`` excludes the units currently installed in
        an asset (``available = on_hand - installed``). Display-only — this does
        not touch the aggregate ``current_stock`` / generic reorder path.
        """
        if not obj.is_serialized:
            return None
        from inventory.services.component_forecast import stock_split_for_item

        return stock_split_for_item(obj)

    def get_price_trend_summary(self, obj):
        """Get price trend summary for the primary supplier.

        ``trend`` is one of ``insufficient_data`` (fewer than two snapshots),
        ``no_data`` (a snapshot records no price at all), ``no_baseline`` (both
        prices known, but the earlier one is ``0.00`` so no percentage exists),
        ``increasing`` / ``decreasing`` / ``stable``. ``direction`` is present
        ONLY on ``no_baseline``, where it is the fact the percentage cannot
        express; everywhere else ``trend`` already carries it.
        """
        from inventory.services.pricing import direction_between, unit_price_of

        primary_supplier = obj.primary_item_supplier
        if not primary_supplier:
            return None

        # Get recent price history (last 5 records)
        recent_history = primary_supplier.price_history.all()[:5]
        if len(recent_history) < 2:
            return {"trend": "insufficient_data", "change_percentage": None}

        latest = recent_history[0]
        previous = recent_history[1]

        # ``is_known``, not truthiness: a supplier that dropped to 0.00 has a
        # price and a trend, and ``if latest.unit_cost and previous.unit_cost``
        # reported "no_data" for it (op-9m2v).
        latest_price = unit_price_of(latest)
        previous_price = unit_price_of(previous)
        if latest_price.is_known and previous_price.is_known:
            change_percentage = latest.price_change_percentage
            if change_percentage is None:
                # BOTH prices are known and only the PERCENTAGE is undefined:
                # the earlier snapshot is 0.00, so there is no baseline to
                # divide by (or the pair shares a ``recorded_at`` and no prior
                # row was found). Three different facts, kept apart (op-9m2v):
                #
                # * ``no_data`` below means a snapshot records NO PRICE AT ALL.
                #   Reusing it here would report "we have no price data" about a
                #   rise from free to $4.00 and throw away the two prices we do
                #   have.
                # * ``stable`` means the price did not move, and arrives as a
                #   real ``Decimal("0")`` percentage.
                # * ``no_baseline`` is this case, and it is the only trend that
                #   carries ``direction``: the percentage has no answer but the
                #   DIRECTION does, and an operator is owed it.
                #
                # Base answered ``{"trend": "no_change", "change_percentage":
                # 0}`` — an undefined number presented as a confident zero.
                return {
                    "trend": "no_baseline",
                    "direction": direction_between(previous_price, latest_price),
                    "change_percentage": None,
                    "latest_cost": latest.unit_cost,
                    "previous_cost": previous.unit_cost,
                    "last_updated": latest.recorded_at,
                }
            elif change_percentage > 0:
                trend = "increasing"
            elif change_percentage < 0:
                trend = "decreasing"
            else:
                trend = "stable"

            return {
                "trend": trend,
                "change_percentage": change_percentage,
                "latest_cost": latest.unit_cost,
                "previous_cost": previous.unit_cost,
                "last_updated": latest.recorded_at,
            }

        return {"trend": "no_data", "change_percentage": None}


class KitComponentSerializer(serializers.ModelSerializer):
    """One line of a kit's bill of materials (op-8n0).

    Used nested-writable on :class:`KitSerializer` so the kit form saves the
    whole bill of materials in a single request; there is deliberately no
    standalone ``/kit-components/`` endpoint in phase 1, the same call already
    made for :class:`PackagingLevelSerializer`.
    """

    component_name = serializers.CharField(source="component.name", read_only=True)
    component_sku = serializers.CharField(source="component.sku", read_only=True)
    component_current_stock = serializers.IntegerField(
        source="component.current_stock", read_only=True
    )
    component_needs_reorder = serializers.BooleanField(
        source="component.needs_reorder", read_only=True
    )

    class Meta:
        model = KitComponent
        fields = [
            "id",
            "component",
            "component_name",
            "component_sku",
            "component_current_stock",
            "component_needs_reorder",
            "quantity",
            "notes",
        ]

    def validate_quantity(self, value):
        """A component quantity of zero would credit nothing on receipt."""
        if value < 1:
            raise serializers.ValidationError("Component quantity must be at least 1.")
        return value


class KitSerializer(InventoryItemSerializer):
    """A kit SKU and its bill of materials (op-8n0).

    Deliberately SUBCLASSES :class:`InventoryItemSerializer` rather than
    redeclaring the catalog fields: a kit *is* an ``InventoryItem`` with
    ``is_kit=True``, and inheriting the whole field set here is what proves that
    design carries — name, SKU, category, image, supplier accessors and
    ownership all behave identically without a line of duplication.
    """

    components = KitComponentSerializer(source="kit_components", many=True, required=False)
    component_count = serializers.SerializerMethodField()
    supplier_terms = serializers.DictField(write_only=True, required=False)

    class Meta(InventoryItemSerializer.Meta):
        fields = InventoryItemSerializer.Meta.fields + [
            "is_kit",
            "components",
            "component_count",
            "supplier_terms",
        ]

    def get_component_count(self, obj):
        """How many distinct component rows the kit contains."""
        return len(obj.kit_components.all())

    def validate(self, attrs):
        """Enforce the kit rules the database and ``clean()`` cannot reach.

        The DB constraints cover self-reference and positive quantities, and
        ``KitComponent.clean()`` covers nested kits,
        but both surface as a 500-shaped ``IntegrityError`` or a non-field
        error. Checking here turns each into a field-addressed 400 the kit form
        can render beside the offending row.

        "At least one component" deliberately lives *only* here and not in the
        database: the kit row has to exist before its components can reference
        it, so no constraint can express it. The receive service therefore also
        tolerates an empty bill of materials rather than trusting this.
        """
        attrs = super().validate(attrs)

        # A kit is a purchasing construct: it is stocked through its components.
        instance = self.instance
        is_serialized = attrs.get("is_serialized", getattr(instance, "is_serialized", False))
        if is_serialized:
            raise serializers.ValidationError(
                {"is_serialized": "A kit cannot be serialized; its components are stocked, not it."}
            )
        current_stock = attrs.get("current_stock", getattr(instance, "current_stock", 0))
        if current_stock:
            raise serializers.ValidationError(
                {
                    "current_stock": (
                        "A kit cannot carry stock — receiving a kit credits its "
                        "component items instead."
                    )
                }
            )

        components = attrs.get("kit_components")
        if components is None:
            # Partial update that does not mention components: leave the
            # existing bill of materials alone.
            if instance is None:
                raise serializers.ValidationError(
                    {"components": "A kit must contain at least one component."}
                )
            return attrs

        if not components:
            raise serializers.ValidationError(
                {"components": "A kit must contain at least one component."}
            )

        seen = set()
        for row in components:
            component = row.get("component")
            if component is None:
                continue
            if component.pk in seen:
                raise serializers.ValidationError(
                    {
                        "components": (
                            f"'{component.name}' is listed more than once — "
                            "combine them into a single row with a higher quantity."
                        )
                    }
                )
            seen.add(component.pk)

            if instance is not None and component.pk == instance.pk:
                raise serializers.ValidationError({"components": "A kit cannot contain itself."})
            if component.is_kit:
                raise serializers.ValidationError(
                    {"components": f"'{component.name}' is a kit — kits cannot contain kits."}
                )
        return attrs

    def _apply_components(self, instance, components):
        """Replace the kit's bill of materials, upserting on ``component``.

        Upsert on the natural key rather than delete-and-recreate so a row that
        survives an edit keeps its primary key — the same reasoning as
        ``_apply_packaging_levels``. Here it matters because the kit editor
        addresses rows by id when patching its local state, and recreating them
        would make every save look like "everything changed".
        """
        if components is None:
            return
        with transaction.atomic():
            keep = set()
            for row in components:
                obj, _ = KitComponent.objects.update_or_create(
                    kit=instance,
                    component=row["component"],
                    defaults={
                        "quantity": row.get("quantity", 1),
                        "notes": row.get("notes", ""),
                    },
                )
                keep.add(obj.pk)
            instance.kit_components.exclude(pk__in=keep).delete()

    def _apply_supplier_terms(self, instance, terms):
        """Attach the purchase terms that make the kit buyable.

        A kit with no ``ItemSupplier`` cannot appear on a purchase order at all,
        because ``PurchaseOrderItem`` points at the supplier relationship rather
        than the item. Folding the terms into the kit create keeps "define a
        kit" a single request; the generic ``/item-suppliers/`` endpoint still
        works for editing them afterwards.
        """
        if not terms:
            return
        supplier_id = terms.get("supplier")
        if supplier_id is None:
            raise serializers.ValidationError(
                {"supplier_terms": {"supplier": "This field is required."}}
            )
        defaults = {
            key: terms[key]
            for key in ("supplier_sku", "supplier_url", "unit_cost", "average_lead_time")
            if key in terms
        }
        defaults.setdefault("quantity_per_package", 1)
        defaults["is_primary"] = True
        ItemSupplier.objects.update_or_create(
            item=instance,
            supplier_id=supplier_id,
            defaults=defaults,
        )

    def create(self, validated_data):
        validated_data["is_kit"] = True
        components = validated_data.pop("kit_components", None)
        terms = validated_data.pop("supplier_terms", None)
        instance = super().create(validated_data)
        self._apply_components(instance, components)
        self._apply_supplier_terms(instance, terms)
        return instance

    def update(self, instance, validated_data):
        validated_data["is_kit"] = True
        components = validated_data.pop("kit_components", None)
        terms = validated_data.pop("supplier_terms", None)
        instance = super().update(instance, validated_data)
        self._apply_components(instance, components)
        self._apply_supplier_terms(instance, terms)
        return instance


class KitSummarySerializer(serializers.ModelSerializer):
    """Compact "this component comes in these kits" row (op-8n0).

    Used by ``/api/inventory/items/{id}/kits/`` and the item detail page's
    "Supplied by kits" card. Deliberately not the full :class:`KitSerializer`:
    the caller is looking at a cartridge and wants the name, the price and how
    many it gets, not the kit's whole catalog record.
    """

    quantity_in_kit = serializers.SerializerMethodField()
    supplier_name = serializers.SerializerMethodField()
    supplier_sku = serializers.SerializerMethodField()
    unit_cost = serializers.SerializerMethodField()
    component_count = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "name",
            "sku",
            "is_active",
            "quantity_in_kit",
            "supplier_name",
            "supplier_sku",
            "unit_cost",
            "component_count",
        ]

    def _primary(self, obj):
        return obj.primary_item_supplier

    def get_quantity_in_kit(self, obj):
        """How many of *this* component one kit contains.

        ``component_id`` is passed through the serializer context by the view
        that already knows which component was asked about, so this costs no
        extra query per row.
        """
        component_id = self.context.get("component_id")
        if component_id is None:
            return None
        for row in obj.kit_components.all():
            if row.component_id == component_id:
                return row.quantity
        return None

    def get_component_count(self, obj):
        return len(obj.kit_components.all())

    def get_supplier_name(self, obj):
        primary = self._primary(obj)
        return primary.supplier.name if primary else None

    def get_supplier_sku(self, obj):
        primary = self._primary(obj)
        return primary.supplier_sku if primary else None

    def get_unit_cost(self, obj):
        """What one of this component costs from the vendor we would buy through.

        Through the ONE price derivation (op-9m2v) rather than off the link,
        so this row cannot drift from ``InventoryItem.unit_cost`` or from
        ``item_metrics``. No value moves: it was already ``None`` for an
        unpriced or supplier-less component.
        """
        from inventory.services.pricing import order_unit_price

        return order_unit_price(obj).amount


class DemandForecastSerializer(serializers.ModelSerializer):
    """Read serializer for a stored :class:`~inventory.models.DemandForecast` row.

    Exposes every stored field plus the item's ``item_name`` / ``sku`` /
    ``category_name`` (mirroring the ``serialized_forecast`` row payload) so the
    ``demand_forecast`` / ``reorder_alerts`` report actions return a
    self-describing row. Read-only in practice -- rows are written by the
    forecasting task, never through the API.

    The restock-interval fields carry the live signal; the retired v1 quantity
    fields are still emitted (as ``0``/``null`` on current rows) so existing
    consumers keep their keys until they are relabelled.

    ``count_mode`` / ``count_unit`` / ``on_hand_display`` present the item at its
    counting granularity (op-ev14) so a forecast row can be read in cases where
    the item is counted in cases. Presentation only — every stored forecast
    number stays exactly as the engine computed it, in base units.
    """

    item_name = serializers.CharField(source="item.name", read_only=True)
    sku = serializers.CharField(source="item.sku", read_only=True)
    count_mode = serializers.CharField(source="item.count_mode", read_only=True)
    count_unit = serializers.SerializerMethodField()
    on_hand_display = serializers.SerializerMethodField()
    # allow_null so an uncategorised item still emits ``category_name: null``
    # (mirrors the serialized_forecast payload) instead of dropping the key when
    # ``item.category`` is None.
    category_name = serializers.CharField(
        source="item.category.name", read_only=True, allow_null=True
    )

    class Meta:
        model = DemandForecast
        fields = [
            "id",
            "item",
            "item_name",
            "sku",
            "category_name",
            # Count-level presentation (op-ev14) -- display only.
            "count_mode",
            "count_unit",
            "on_hand_display",
            "generated_at",
            # Restock-interval signal (v2).
            "avg_interval_days",
            "interval_samples",
            "last_restock_date",
            "predicted_next_reorder_date",
            "days_until_due",
            # Retired v1 quantity projection -- 0/null on v2 rows, kept so
            # existing consumers don't lose keys mid-flight.
            "horizon_days",
            "predicted_daily_demand",
            "horizon_demand",
            "horizon_demand_upper",
            "days_until_stockout",
            "projected_stockout_date",
            "predictive_reorder_point",
            "safety_stock",
            # Decision + provenance.
            "available_at_generation",
            "needs_reorder",
            "lead_time_days",
            "method",
            "model_version",
        ]

    def get_count_unit(self, obj) -> str:
        from inventory.services.packaging import count_unit

        return count_unit(obj.item)

    def get_on_hand_display(self, obj) -> dict:
        from inventory.services.packaging import on_hand_display

        return on_hand_display(obj.item)


class CommittedBreakdownEntrySerializer(serializers.Serializer):
    """One work order holding part of an item's committed quantity (QC).

    The attribution side of ``quantity_committed``: which job — and so which
    machine — the reserved stock is going to. Entries sum to
    ``quantity_committed`` and arrive oldest work order first.
    """

    work_order_id = serializers.UUIDField()
    work_order_short_id = serializers.CharField()  # e.g. "WO-1A2B3C4D"
    asset_id = serializers.UUIDField(allow_null=True)  # null on an asset-less work order
    asset_name = serializers.CharField(allow_null=True)
    quantity = serializers.FloatField()


class InventoryMetricsSerializer(serializers.Serializer):
    """Computed stock + cost metrics for the inventory-item detail view.

    Powers the ``SKU · QOH · QOO · QA · QC · QIT · RP · Lead · Cost`` row on
    the web item-detail page and the paired ScanTTY TUI row (issue-5). The
    field names below are a pinned contract shared with the ScanTTY worker, so
    do not rename them.

    All values are computed in ``InventoryItemViewSet.metrics``; this
    serializer only shapes the output and is fed a plain ``dict`` (not a model
    instance). Quantities are numbers; money fields are DRF ``DecimalField``s
    and so serialize as STRINGS -- unlike ``InventoryItemSerializer.unit_cost``,
    which is a model property and therefore a ``ReadOnlyField`` handing the raw
    ``Decimal`` to the JSON encoder as a number (op-9m2v).
    """

    current_stock = serializers.IntegerField()  # QOH — on hand
    quantity_on_order = serializers.IntegerField()  # QOO — open PO units
    quantity_available = serializers.FloatField()  # QA — QOH minus QC
    quantity_committed = serializers.FloatField()  # QC — open work-order demand
    committed_breakdown = CommittedBreakdownEntrySerializer(many=True)  # which WOs/assets hold QC
    quantity_in_transit = serializers.IntegerField()  # QIT — partially-received (⊆ QOO)
    reorder_point = serializers.IntegerField()  # RP — reorder_quantity
    lead_time_days = serializers.IntegerField(allow_null=True)  # Lead — average_lead_time
    unit_cost = serializers.DecimalField(  # Cost — per-item, or per-case when case-based
        max_digits=10, decimal_places=2, allow_null=True
    )
    cost_trend = serializers.ChoiceField(choices=["up", "down", "flat", "no_history"])
    last_po_unit_cost = (
        serializers.DecimalField(  # most recent PO unit cost (for the arrow tooltip)
            max_digits=10, decimal_places=4, allow_null=True
        )
    )
    is_case_based = serializers.BooleanField()
    case_size = serializers.IntegerField(allow_null=True)  # units per case (quantity_per_package)
    # Why the Cost / Lead above may be blank or unbacked (op-2rsp). The scoring
    # neither rewards nor punishes a missing price or an empty delivery record,
    # so a supplier can win WITH one — and an operator reading a blank Cost cell
    # would otherwise have to infer whether the system knew the price and chose
    # anyway. Both are ``false`` when an operator's own flagged primary took the
    # gate, because nothing was weighed against anything there.
    supplier_scored_without_price = serializers.BooleanField()
    supplier_scored_without_history = serializers.BooleanField()


class ItemOrderCostSerializer(serializers.Serializer):
    """One purchase-order line for an item — the per-order unit cost (op-96uo).

    Fed a plain ``dict`` built in ``InventoryItemViewSet.purchase_history``.
    Money is a DRF ``DecimalField`` so it serializes as a string, matching
    ``InventoryMetricsSerializer.last_po_unit_cost`` and the item serializer's
    ``unit_cost``. Field names are a pinned contract shared with the web item
    detail page and the ScanTTY TUI, so do not rename them.

    ``purchase_order`` (the PO pk) is carried alongside ``po_number`` because
    ``po_number`` is nullable — clients that group rows by order need a key
    that is always present.
    """

    purchase_order = serializers.IntegerField()
    po_number = serializers.CharField(allow_null=True)
    order_date = serializers.DateTimeField()
    status = serializers.CharField()
    quantity_ordered = serializers.IntegerField()
    unit_cost_ordered = serializers.DecimalField(max_digits=10, decimal_places=4)
    unit_cost_actual = serializers.DecimalField(max_digits=10, decimal_places=4, allow_null=True)


class ItemDeliverySerializer(serializers.Serializer):
    """One delivery of an item — tracking number + receipt record (op-96uo).

    One row per ``DeliveryItem``: a partially-shipped order produces several
    rows for the same ``po_number``, each with its own tracking number, which
    is exactly how "one order, many tracking numbers" is meant to render.
    Pinned contract; see :class:`ItemOrderCostSerializer`.
    """

    purchase_order = serializers.IntegerField()
    po_number = serializers.CharField(allow_null=True)
    delivery_date = serializers.DateTimeField()
    tracking_number = serializers.CharField(allow_blank=True)
    carrier = serializers.CharField(allow_blank=True)
    quantity_received = serializers.IntegerField()
    receipt_notes = serializers.CharField(allow_blank=True)
    is_complete = serializers.BooleanField()


class AssetPartSerializer(serializers.ModelSerializer):
    """Serializer for asset parts/consumables."""

    part_name = serializers.CharField(source="part.name", read_only=True)
    part_sku = serializers.CharField(source="part.sku", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)

    # Calculated properties
    days_since_replacement = serializers.ReadOnlyField()
    needs_replacement = serializers.ReadOnlyField()

    # Part details (nested)
    part_details = serializers.SerializerMethodField()

    class Meta:
        model = AssetPart
        fields = [
            "id",
            "asset",
            "asset_name",
            "asset_tag",
            "part",
            "part_name",
            "part_sku",
            "quantity_needed",
            "is_required",
            "maintenance_interval_days",
            "last_replaced_at",
            "replacement_serial_number",
            "days_since_replacement",
            "needs_replacement",
            "notes",
            "part_details",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "days_since_replacement",
            "needs_replacement",
            "created_at",
            "updated_at",
        ]

    def get_part_details(self, obj):
        """Return basic details about the part inventory item."""
        part = obj.part
        return {
            "id": str(part.id),
            "name": part.name,
            "sku": part.sku,
            "current_stock": part.current_stock,
            "minimum_stock": part.minimum_stock,
            "needs_reorder": part.needs_reorder,
            "category_name": part.category.name if part.category else None,
            "is_serialized": part.is_serialized,
        }


class AssetMeterSerializer(serializers.ModelSerializer):
    """Serializer for an asset's usage meters (EAM bead-1).

    ``current_value`` / ``current_is_estimated`` / ``rollup_watermark_at`` are
    server-controlled — they only move when a reading is applied (auto rollup or
    a manual record-reading / adjust action), so they are read-only here. The
    definition fields (``name``, ``meter_type``, ``unit``, ``source``,
    ``is_active``) round-trip so meters can be created/edited via CRUD.

    Defined before :class:`AssetSerializer` so it can be embedded there as the
    nested read-only ``meters`` field.
    """

    meter_type_display = serializers.CharField(source="get_meter_type_display", read_only=True)
    source_display = serializers.CharField(source="get_source_display", read_only=True)

    class Meta:
        model = AssetMeter
        fields = [
            "id",
            "asset",
            "name",
            "meter_type",
            "meter_type_display",
            "unit",
            "source",
            "source_display",
            "current_value",
            "current_is_estimated",
            "rollup_watermark_at",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "current_value",
            "current_is_estimated",
            "rollup_watermark_at",
            "created_at",
            "updated_at",
        ]


class AssetMeterReadingSerializer(serializers.ModelSerializer):
    """Read-only serializer for the append-only meter reading ledger (EAM bead-1).

    Readings are created only through :func:`inventory.services.meter_sources.apply_reading`
    (via the rollup or the record-reading / adjust actions), never by a direct
    POST to this serializer — every field is read-only.
    """

    source_display = serializers.CharField(source="get_source_display", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AssetMeterReading
        fields = [
            "id",
            "meter",
            "source",
            "source_display",
            "delta",
            "value_after",
            "is_estimated",
            "observed_at",
            "recorded_at",
            "recorded_by",
            "recorded_by_name",
            "source_ref",
            "notes",
        ]
        read_only_fields = fields

    def get_recorded_by_name(self, obj):
        if obj.recorded_by:
            return obj.recorded_by.get_full_name() or obj.recorded_by.username
        return None


class AssetSerializer(serializers.ModelSerializer):
    """Serializer for hard asset tracking."""

    # Related field names for display
    inventory_item_name = serializers.CharField(source="inventory_item.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    manufacturer_name_display = serializers.CharField(source="manufacturer.name", read_only=True)

    # Calculated properties
    display_manufacturer = serializers.ReadOnlyField()
    acquisition_display = serializers.ReadOnlyField()
    age_in_days = serializers.ReadOnlyField()

    # Image/file URLs
    image_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    qr_code_url = serializers.SerializerMethodField()
    qr_code_scan_url = serializers.SerializerMethodField()
    manual_pdf_url = serializers.SerializerMethodField()

    # ForgeKey-related fields (from forgekey app)
    operational_mode = serializers.SerializerMethodField()
    is_locked = serializers.SerializerMethodField()
    lockout_info = serializers.SerializerMethodField()
    owning_group_name = serializers.SerializerMethodField()
    owning_user_name = serializers.SerializerMethodField()

    # Authorization fields
    can_enable = serializers.SerializerMethodField()
    can_unlock = serializers.SerializerMethodField()

    # Parts/consumables
    parts = AssetPartSerializer(source="asset_parts", many=True, read_only=True)

    # Usage meters (EAM bead-1) — nested read-only so the asset-detail payload
    # carries them without a second round-trip. Writes go through the dedicated
    # asset-meters endpoint. This is also the additive contract the ScanTTY
    # asset-meters follow-up displays against.
    meters = AssetMeterSerializer(many=True, read_only=True)

    # Power / electrical computed flag
    is_forgekey_managed = serializers.ReadOnlyField()

    # Read-only summary of the asset's breaker + disconnect; writes still
    # go through the dedicated FK fields.
    breaker_summary = serializers.SerializerMethodField()
    disconnect_summary = serializers.SerializerMethodField()

    # Operational/site-requirements fields now live on the 1:1
    # facilities.AssetSiteRequirements profile (#880). They are declared
    # explicitly (rather than auto-built from the model) because they resolve
    # through Asset compat properties, not real model fields. The JSON keys +
    # shapes are unchanged, so the SPA + ScanTTY need no change. Reads go
    # through the properties; writes are routed into the profile by
    # ``create``/``update`` below. ``circuit`` is now a read-only
    # breaker-derived label.
    breaker = serializers.PrimaryKeyRelatedField(
        queryset=PowerBreaker.objects.all(), required=False, allow_null=True
    )
    disconnect = serializers.PrimaryKeyRelatedField(
        queryset=Disconnect.objects.all(), required=False, allow_null=True
    )
    needs_compressed_air = serializers.BooleanField(required=False)
    needs_ventilation = serializers.BooleanField(required=False)
    generates_heat_or_flame = serializers.BooleanField(required=False)
    needs_chilling = serializers.BooleanField(required=False)
    special_requirements = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )
    work_safety_notes = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )
    circuit = serializers.CharField(read_only=True)

    # Required certifications — IDs round-trip for writes; the *_details
    # array carries name + SIG so the SPA + e-paper render don't need a
    # second round-trip per cert lookup.
    required_certification_details = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "name",
            "description",
            "serial_number",
            "asset_tag",
            # Relationships
            "inventory_item",
            "inventory_item_name",
            "category",
            "category_name",
            "location",
            "location_name",
            # Manufacturer
            "manufacturer",
            "manufacturer_name",
            "manufacturer_name_display",
            "display_manufacturer",
            # Acquisition
            "date_received",
            "amount_paid",
            "is_donation",
            "donor_name",
            "acquisition_display",
            "age_in_days",
            # Cost recovery (landlord billing)
            "is_cost_recoverable",
            # Product info
            "product_url",
            "wiki_page_url",
            # Maintenance
            # NOTE: maintenance_plan is a legacy free-text field kept on the
            # model + admin form for back-compat. The asset detail page no
            # longer renders it — scheduled maintenance lives on
            # MaintenanceItem and unscheduled work on WorkOrder. See oms-4mk.
            "maintenance_plan",
            # Parts/consumables
            "parts",
            # Usage meters (EAM bead-1)
            "meters",
            # Operational / site requirements (facilities.AssetSiteRequirements)
            "circuit",
            "needs_compressed_air",
            "needs_ventilation",
            "generates_heat_or_flame",
            "needs_chilling",
            "special_requirements",
            "work_safety_notes",
            "is_chargeable",
            "mac_address",
            # Power / electrical
            "breaker",
            "breaker_summary",
            "disconnect",
            "disconnect_summary",
            "power_draw_watts",
            "wiring_type",
            "suite",
            "electrical_box",
            "breaker_location",
            "has_interlock",
            "interlock_type",
            "interlock_responsible",
            "lockout_type",
            "lockout_instructions",
            "lockout_responsible",
            "has_network_drop",
            "network_drop_location",
            "is_forgekey_managed",
            # Scanning tracking
            "last_scanned_at",
            # Group ownership
            "owning_group",
            "owning_group_name",
            "owning_user_name",
            "groups_can_enable",
            # ForgeKey fields (operational mode and lockout status)
            "operational_mode",
            "is_locked",
            "lockout_info",
            # Authorization
            "can_enable",
            "can_unlock",
            # Media
            "image",
            "image_url",
            "thumbnail_url",
            "manual_pdf",
            "manual_pdf_url",
            "qr_code",
            "qr_code_url",
            "qr_code_scan_url",
            # Training / certification
            "training_required",
            "required_certifications",
            "required_certification_details",
            # Status
            "status",
            # NOTE: condition_notes is a legacy free-text field kept on the
            # model + admin form for back-compat. The asset detail page no
            # longer renders it — current condition is reflected by
            # AssetProblem (Problem History) and WorkOrder. See oms-4mk.
            "condition_notes",
            # Metadata
            "is_active",
            "report_only",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "asset_tag",
            "qr_code",
            "last_scanned_at",
            "operational_mode",
            "is_locked",
            "lockout_info",
            "owning_group_name",
            "owning_user_name",
            "can_enable",
            "can_unlock",
            "qr_code_url",
            "qr_code_scan_url",
            "is_forgekey_managed",
            "required_certification_details",
            "created_at",
            "updated_at",
        ]

    # Keys that live on the 1:1 facilities.AssetSiteRequirements profile but are
    # flattened onto the Asset payload for API back-compat (#880). Writes to
    # these keys are diverted from the Asset into the profile.
    _PROFILE_FIELDS = (
        "breaker",
        "disconnect",
        "needs_compressed_air",
        "needs_ventilation",
        "generates_heat_or_flame",
        "needs_chilling",
        "special_requirements",
        "work_safety_notes",
    )

    def _pop_profile_fields(self, validated_data):
        return {
            key: validated_data.pop(key) for key in self._PROFILE_FIELDS if key in validated_data
        }

    def _apply_profile(self, asset, profile_data):
        from facilities.models import AssetSiteRequirements

        profile, _ = AssetSiteRequirements.objects.update_or_create(
            asset=asset, defaults=profile_data
        )
        # Refresh the reverse-relation cache so a follow-up read and the loto
        # post_save derivation see the new values without a stale-cache miss.
        asset.site_requirements = profile

    def create(self, validated_data):
        profile_data = self._pop_profile_fields(validated_data)
        asset = super().create(validated_data)
        if profile_data:
            self._apply_profile(asset, profile_data)
            # Re-fire the asset post_save so downstream derivations (loto) run
            # now that the breaker is resolvable through the profile.
            asset.save()
        return asset

    def update(self, instance, validated_data):
        profile_data = self._pop_profile_fields(validated_data)
        if profile_data:
            # Upsert the profile first so the reverse cache is fresh before
            # super().update() saves the asset and fires the derivation signal.
            self._apply_profile(instance, profile_data)
        return super().update(instance, validated_data)

    def get_required_certification_details(self, obj):
        # Consume the AssetViewSet's prefetched `required_certifications__sig`
        # in-memory — calling .filter() / .select_related() here would build
        # a new queryset that bypasses the prefetch cache and reintroduce an
        # N+1 across the asset list endpoint (gh: test_asset_list_is_bounded).
        return [
            {
                "id": cert.id,
                "name": cert.name,
                "slug": cert.slug,
                "sig_name": cert.sig.name,
            }
            for cert in obj.required_certifications.all()
            if cert.is_active
        ]

    def get_operational_mode(self, obj):
        """Get operational mode from forgekey app."""
        try:
            from forgekey.models import OperationalMode

            mode = OperationalMode.objects.get(asset=obj)
            return {
                "mode": mode.mode,
                "classroom_mode_enabled": mode.classroom_mode_enabled,
            }
        except OperationalMode.DoesNotExist:
            return {"mode": "available", "classroom_mode_enabled": False}

    def get_is_locked(self, obj):
        """Check if asset is locked via forgekey lockouts."""
        try:
            from forgekey.models import DeviceLockout

            return DeviceLockout.objects.filter(asset=obj, is_active=True).exists()
        except Exception:
            return False

    def get_lockout_info(self, obj):
        """Get lockout information from forgekey app."""
        try:
            from forgekey.models import DeviceLockout

            active_lockout = DeviceLockout.objects.filter(asset=obj, is_active=True).first()
            if active_lockout:
                return {
                    "locked_by": (
                        active_lockout.locked_by.username if active_lockout.locked_by else None
                    ),
                    "locked_at": (
                        active_lockout.locked_at.isoformat() if active_lockout.locked_at else None
                    ),
                    "lockout_level": active_lockout.lockout_level,
                    "reason": active_lockout.reason,
                }
            return None
        except Exception:
            return None

    def get_image_url(self, obj):
        """Return the image URL when available."""
        try:
            return obj.image.url if obj.image else None
        except Exception:
            return None

    def get_thumbnail_url(self, obj):
        """Return the thumbnail URL when available."""
        try:
            if obj.thumbnail:
                request = self.context.get("request")
                if request:
                    return request.build_absolute_uri(obj.thumbnail.url)
                return obj.thumbnail.url
            return None
        except Exception:
            return None

    def get_qr_code_url(self, obj):
        """Return the QR code image URL when available."""
        try:
            return obj.qr_code.url if obj.qr_code else None
        except Exception:
            return None

    def get_qr_code_scan_url(self, obj):
        """Return the scan URL that the QR code points to."""
        from django.conf import settings

        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        return f"{frontend_url}/scan/asset/{obj.id}"

    def get_owning_group_name(self, obj):
        """Return owning group name, or 'Logistics' if owned by space."""
        if obj.ownership_type == obj.OwnershipType.SPACE:
            return "Logistics"
        if obj.owning_group:
            return obj.owning_group.name
        return None

    def get_owning_user_name(self, obj):
        """Return owning user name, or 'COO' if owned by space."""
        if obj.ownership_type == obj.OwnershipType.SPACE:
            return "COO"
        if obj.owning_user:
            return obj.owning_user.username
        return None

    def get_can_enable(self, obj):
        """Check if the current user can enable this asset."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False

        # Report-only assets cannot be enabled
        if obj.report_only:
            return False

        user = request.user

        # Admins can always enable
        if obj.is_user_admin(user):
            return True

        # Check if user can operate assets in Implementing/Testing status
        if obj.status in [obj.Status.IMPLEMENTING, obj.Status.TESTING]:
            return obj.can_user_operate(user)

        # Check if user's groups are in groups_can_enable
        user_groups = user.groups.all()
        if obj.groups_can_enable.exists():
            return any(group in obj.groups_can_enable.all() for group in user_groups)

        # If no groups specified, default to allowing (for backward compatibility)
        return True

    def get_can_unlock(self, obj):
        """Check if the current user can lock or unlock this asset."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False

        # Report-only assets cannot be locked/unlocked
        if obj.report_only:
            return False

        user = request.user

        # Admins can always lock/unlock
        if obj.is_user_admin(user):
            return True

        # Check if asset is locked
        try:
            from forgekey.models import DeviceLockout

            active_lockouts = DeviceLockout.objects.filter(asset=obj, is_active=True)

            if active_lockouts.exists():
                # Check if user can unlock any of the lockouts
                for lockout in active_lockouts:
                    if lockout.can_be_unlocked_by(user):
                        return True
                return False
            else:
                # Asset is not locked - check if user can lock it
                # For now, allow locking if user is in logistics or has group permissions
                # This can be customized based on your requirements
                if obj.is_user_in_logistics(user):
                    return True
                # Check if user is in a group that can enable this asset
                user_groups = user.groups.all()
                if obj.groups_can_enable.exists():
                    return any(group in obj.groups_can_enable.all() for group in user_groups)
                return False
        except Exception:
            return False

    def get_manual_pdf_url(self, obj):
        """Return the manual PDF URL when available."""
        try:
            return obj.manual_pdf.url if obj.manual_pdf else None
        except Exception:
            return None

    def get_breaker_summary(self, obj):
        if obj.breaker_id is None:
            return None
        b = obj.breaker
        panel = b.panel
        return {
            "id": b.pk,
            "panel_id": panel.pk if panel else None,
            "panel_name": panel.name if panel else "",
            "position": b.position,
            "amperage": b.amperage,
            "label": b.label,
        }

    def get_disconnect_summary(self, obj):
        if obj.disconnect_id is None:
            return None
        d = obj.disconnect
        return {
            "id": d.pk,
            "label": d.label,
            "disconnect_type": d.disconnect_type,
            "is_lockable": d.is_lockable,
        }


class AssetProblemPhotoSerializer(serializers.ModelSerializer):
    """Serializer for photos attached to an asset problem report."""

    uploaded_by_name = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = AssetProblemPhoto
        fields = [
            "id",
            "problem",
            "image",
            "image_url",
            "caption",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
        ]
        read_only_fields = [
            "problem",
            "uploaded_at",
            "uploaded_by",
            "uploaded_by_name",
            "image_url",
        ]

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        if obj.image:
            return obj.image.url
        return None


class AssetDocumentSerializer(serializers.ModelSerializer):
    """Read+write serializer for an asset's document-library entries.

    ``version``/``is_current`` are server-controlled (set by the viewset when a
    document supersedes an earlier one), so they are read-only here. ``file`` is
    uploaded via multipart; ``file_url`` exposes an absolute URL for the web/TUI
    clients. ``supersedes`` is writable so an "upload a new version" request can
    link back to the document it replaces.
    """

    file_url = serializers.SerializerMethodField()
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    supersedes_title = serializers.SerializerMethodField()

    class Meta:
        model = AssetDocument
        fields = [
            "id",
            "asset",
            "file",
            "file_url",
            "category",
            "category_display",
            "title",
            "description",
            "version",
            "is_current",
            "supersedes",
            "supersedes_title",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
        ]
        read_only_fields = [
            "version",
            "is_current",
            "uploaded_by",
            "uploaded_at",
        ]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        if obj.file:
            return obj.file.url
        return None

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None

    def get_supersedes_title(self, obj):
        if obj.supersedes:
            return f"{obj.supersedes.title} (v{obj.supersedes.version})"
        return None

    def validate(self, attrs):
        """Guard against superseding a document that belongs to another asset."""
        supersedes = attrs.get("supersedes")
        asset = attrs.get("asset")
        if supersedes is not None and asset is not None and supersedes.asset_id != asset.id:
            raise serializers.ValidationError(
                {"supersedes": "The superseded document must belong to the same asset."}
            )
        return attrs


class AffectedAssetPartSerializer(serializers.ModelSerializer):
    """Compact read-only view of an AssetPart, surfaced on problem reports so
    maintenance can see WHICH components the reporter flagged for replace/fix."""

    part_name = serializers.CharField(source="part.name", read_only=True)
    part_sku = serializers.CharField(source="part.sku", read_only=True)

    class Meta:
        model = AssetPart
        fields = ["id", "part_name", "part_sku", "quantity_needed", "is_required"]
        read_only_fields = fields


class AssetProblemSerializer(serializers.ModelSerializer):
    """Serializer for asset problem reports."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    photos = AssetProblemPhotoSerializer(many=True, read_only=True)
    affected_parts = AffectedAssetPartSerializer(many=True, read_only=True)
    part_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        write_only=True,
        required=False,
        queryset=AssetPart.objects.all(),
        source="affected_parts",
        help_text="AssetPart ids the reporter flagged as needing replace/fix.",
    )
    work_order_short_id = serializers.SerializerMethodField()
    third_party_work_order_short_id = serializers.SerializerMethodField()

    class Meta:
        model = AssetProblem
        fields = [
            "id",
            "asset",
            "asset_name",
            "asset_tag",
            "reported_by",
            "description",
            "status",
            "work_order",
            "work_order_short_id",
            "third_party_work_order",
            "third_party_work_order_short_id",
            "resolution_notes",
            "created_at",
            "updated_at",
            "resolved_at",
            "resolved_by",
            "photos",
            "affected_parts",
            "part_ids",
        ]
        read_only_fields = [
            "created_at",
            "updated_at",
            "resolved_at",
            "work_order",
            "third_party_work_order",
        ]

    def get_work_order_short_id(self, obj):
        return obj.work_order.short_id if obj.work_order_id else None

    def get_third_party_work_order_short_id(self, obj):
        return obj.third_party_work_order.short_id if obj.third_party_work_order_id else None


class LocationProblemSerializer(serializers.ModelSerializer):
    """Serializer for location problem reports."""

    location_name = serializers.CharField(source="location.name", read_only=True)
    photo_url = serializers.SerializerMethodField()
    paper_form_url = serializers.SerializerMethodField()
    work_order_short_id = serializers.SerializerMethodField()
    third_party_work_order_short_id = serializers.SerializerMethodField()
    severity_display = serializers.CharField(source="get_severity_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = LocationProblem
        fields = [
            "id",
            "location",
            "location_name",
            "reported_by",
            "description",
            "status",
            "status_display",
            "severity",
            "severity_display",
            "photo",
            "photo_url",
            "paper_form_attachment",
            "paper_form_url",
            "work_order",
            "work_order_short_id",
            "third_party_work_order",
            "third_party_work_order_short_id",
            "resolution_notes",
            "reported_at",
            "updated_at",
            "resolved_at",
            "resolved_by",
        ]
        read_only_fields = [
            "id",
            "reported_at",
            "updated_at",
            "resolved_at",
            "work_order",
            "third_party_work_order",
        ]

    def _absolute(self, file_field):
        if not file_field:
            return None
        request = self.context.get("request")
        try:
            url = file_field.url
        except Exception:
            return None
        if request:
            return request.build_absolute_uri(url)
        return url

    def get_photo_url(self, obj):
        return self._absolute(obj.photo)

    def get_paper_form_url(self, obj):
        return self._absolute(obj.paper_form_attachment)

    def get_work_order_short_id(self, obj):
        return obj.work_order.short_id if obj.work_order else None

    def get_third_party_work_order_short_id(self, obj):
        return obj.third_party_work_order.short_id if obj.third_party_work_order else None


class MaintenanceMaterialSerializer(serializers.ModelSerializer):
    """Serializer for materials needed for a maintenance task."""

    total_estimated_cost = serializers.ReadOnlyField()
    inventory_item_detail = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceMaterial
        fields = [
            "id",
            "maintenance_item",
            "inventory_item",
            "inventory_item_detail",
            "name",
            "quantity",
            "unit",
            "estimated_cost_per_unit",
            "total_estimated_cost",
            "notes",
            "created_at",
        ]
        read_only_fields = ["total_estimated_cost", "inventory_item_detail", "created_at"]

    def get_inventory_item_detail(self, obj):
        item = obj.inventory_item
        if item is None:
            return None
        return {
            "id": str(item.id),
            "name": item.name,
            "current_stock": item.current_stock,
            "minimum_stock": item.minimum_stock,
            "reorder_quantity": item.reorder_quantity,
        }


class MaintenanceToolSerializer(serializers.ModelSerializer):
    """Serializer for tools needed to perform a maintenance task.

    Mirrors its consumable sibling :class:`MaintenanceMaterialSerializer`
    field-for-field in style. The field names are a pinned contract: ScanTTY
    decodes this exact payload to print the tool list on the e-paper work
    order, so do not rename them.
    """

    inventory_item_detail = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceTool
        fields = [
            "id",
            "maintenance_item",
            "inventory_item",
            "inventory_item_detail",
            "name",
            "quantity",
            "location_hint",
            "is_required",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "inventory_item_detail", "created_at"]

    def get_inventory_item_detail(self, obj):
        item = obj.inventory_item
        if item is None:
            return None
        return {
            "id": str(item.id),
            "name": item.name,
            "current_stock": item.current_stock,
            "minimum_stock": item.minimum_stock,
            "reorder_quantity": item.reorder_quantity,
        }


class MaintenanceTaskSerializer(serializers.ModelSerializer):
    """Serializer for ordered sub-task steps within a maintenance item.

    ``reference_image`` is the step's instructional photo: write it as a
    multipart file, read it back as an absolute ``reference_image_url``. The
    field names are a pinned contract — ScanTTY decodes these exact keys.
    """

    reference_image_url = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceTask
        fields = [
            "id",
            "maintenance_item",
            "order",
            "title",
            "description",
            "is_required",
            "reference_image",
            "reference_image_url",
            "created_at",
        ]
        read_only_fields = ["created_at", "reference_image_url"]
        extra_kwargs = {
            # File in, URL out: the raw storage path is never useful to a
            # client, and making it write-only keeps the read contract to the
            # single absolute ``reference_image_url``. ``allow_null`` so an
            # editor can clear the photo off a step.
            "reference_image": {"write_only": True, "required": False, "allow_null": True},
        }

    def get_reference_image_url(self, obj):
        """Absolute URL of this step's reference photo, or None."""
        request = self.context.get("request")
        if obj.reference_image and request:
            return request.build_absolute_uri(obj.reference_image.url)
        return None


class MaintenanceItemSerializer(serializers.ModelSerializer):
    """Serializer for preventive maintenance tasks associated with an asset."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    materials = MaintenanceMaterialSerializer(many=True, read_only=True)
    tools = MaintenanceToolSerializer(many=True, read_only=True)
    tasks = MaintenanceTaskSerializer(many=True, read_only=True)
    is_overdue = serializers.ReadOnlyField()
    days_overdue = serializers.ReadOnlyField()
    next_due_at = serializers.ReadOnlyField()

    class Meta:
        model = MaintenanceItem
        fields = [
            "id",
            "asset",
            "asset_name",
            "asset_tag",
            "title",
            "description",
            "instructions",
            "estimated_time_minutes",
            "estimated_cost",
            "interval_days",
            "last_completed_at",
            "is_active",
            "is_overdue",
            "days_overdue",
            "next_due_at",
            "materials",
            "tools",
            "tasks",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "is_overdue",
            "days_overdue",
            "next_due_at",
            "created_at",
            "updated_at",
        ]


class MaintenanceLogSerializer(serializers.ModelSerializer):
    """Serializer for maintenance completion records."""

    completed_by_name = serializers.SerializerMethodField()
    maintenance_item_title = serializers.CharField(source="maintenance_item.title", read_only=True)
    asset_name = serializers.CharField(source="maintenance_item.asset.name", read_only=True)

    class Meta:
        model = MaintenanceLog
        fields = [
            "id",
            "maintenance_item",
            "maintenance_item_title",
            "asset_name",
            "completed_by",
            "completed_by_name",
            "completed_at",
            "time_spent_minutes",
            "cost_incurred",
            "notes",
            "created_at",
        ]
        read_only_fields = ["completed_at", "created_at", "completed_by_name"]

    def get_completed_by_name(self, obj):
        if obj.completed_by:
            return obj.completed_by.get_full_name() or obj.completed_by.username
        return None


class FixtureSerializer(serializers.ModelSerializer):
    """Serializer for fixtures (refillable assets)."""

    # Display names for related fields
    location_name = serializers.CharField(source="location.name", read_only=True)
    refill_item_name = serializers.CharField(source="refill_item.name", read_only=True)
    refill_item_sku = serializers.CharField(source="refill_item.sku", read_only=True)

    # Calculated fields
    pending_requests_count = serializers.ReadOnlyField()

    # QR code URL
    qr_code_url = serializers.SerializerMethodField()

    class Meta:
        model = Fixture
        fields = [
            "id",
            "name",
            "description",
            "location",
            "location_name",
            "refill_item",
            "refill_item_name",
            "refill_item_sku",
            "asset_tag",
            "is_active",
            "pending_requests_count",
            "qr_code_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_qr_code_url(self, obj):
        """Generate QR code URL for fixture scanning."""
        from django.conf import settings

        base_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        return f"{base_url.rstrip('/')}/scan/fixture/{obj.id}"


class FixtureRefillRequestSerializer(serializers.ModelSerializer):
    """Serializer for fixture refill requests."""

    # Display names for related fields
    fixture_name = serializers.CharField(source="fixture.name", read_only=True)
    fixture_location = serializers.CharField(source="fixture.location.name", read_only=True)
    refill_item_name = serializers.CharField(source="fixture.refill_item.name", read_only=True)
    refill_item_sku = serializers.CharField(source="fixture.refill_item.sku", read_only=True)

    # Calculated fields
    time_to_resolve = serializers.ReadOnlyField()

    # Actor-identity convention (#888). The legacy requested_by/resolved_by
    # strings stay as read-only display outputs (the *_name half of the pair —
    # the frontend and FixtureDetailSerializer.recent_refill_requests read them).
    # The *_actor fields collapse the (user, name) pair through actor_display;
    # the *_username fields expose the raw auth username when the FK is set.
    # All four are additive and read-only — no request-body contract changes.
    requested_actor = serializers.SerializerMethodField()
    resolved_actor = serializers.SerializerMethodField()
    requested_username = serializers.SerializerMethodField()
    resolved_username = serializers.SerializerMethodField()

    class Meta:
        model = FixtureRefillRequest
        fields = [
            "id",
            "fixture",
            "fixture_name",
            "fixture_location",
            "refill_item_name",
            "refill_item_sku",
            "status",
            "requested_at",
            "requested_by",
            "requested_actor",
            "requested_username",
            "resolved_at",
            "resolved_by",
            "resolved_actor",
            "resolved_username",
            "notes",
            "time_to_resolve",
        ]
        read_only_fields = [
            "requested_at",
            "requested_by",
            "resolved_at",
            "resolved_by",
            "time_to_resolve",
        ]

    def get_requested_actor(self, obj) -> str:
        """Collapsed display name for the requester (never null — an anonymous
        scan reads as "Anonymous")."""
        return actor_display(obj.requested_user, obj.requested_by)

    def get_resolved_actor(self, obj):
        """Collapsed display name for the resolver, or None while unresolved."""
        if obj.resolved_user_id is None and not obj.resolved_by:
            return None
        return actor_display(obj.resolved_user, obj.resolved_by)

    def get_requested_username(self, obj):
        """Raw auth username of the requester, or None if anonymous/system."""
        return obj.requested_user.username if obj.requested_user_id else None

    def get_resolved_username(self, obj):
        """Raw auth username of the resolver, or None if anonymous/unresolved."""
        return obj.resolved_user.username if obj.resolved_user_id else None


class FixtureDetailSerializer(FixtureSerializer):
    """Extended fixture serializer with recent refill requests."""

    recent_refill_requests = FixtureRefillRequestSerializer(
        source="refill_requests", many=True, read_only=True
    )
    refill_item_details = serializers.SerializerMethodField()

    class Meta(FixtureSerializer.Meta):
        fields = FixtureSerializer.Meta.fields + [
            "recent_refill_requests",
            "refill_item_details",
        ]

    def get_refill_item_details(self, obj):
        """Return basic details about the refill inventory item."""
        item = obj.refill_item
        return {
            "id": item.id,
            "name": item.name,
            "sku": item.sku,
            "current_stock": item.current_stock,
            "minimum_stock": item.minimum_stock,
            "needs_reorder": item.needs_reorder,
        }

    def to_representation(self, instance):
        """Limit recent requests to last 10."""
        data = super().to_representation(instance)
        if "recent_refill_requests" in data:
            data["recent_refill_requests"] = data["recent_refill_requests"][:10]
        return data


class WorkOrderEvidencePhotoSerializer(serializers.ModelSerializer):
    """The trimmed photo shape a *step* carries — "here is what I did".

    Nested under :class:`WorkOrderTaskCompletionSerializer`, so the work-order
    keys the parent already carries (``work_order``, ``task_completion``) are
    omitted. Pinned contract: ScanTTY decodes these exact five keys.
    """

    uploaded_by_name = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderPhoto
        fields = ["id", "image_url", "caption", "uploaded_at", "uploaded_by_name"]
        read_only_fields = fields

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class WorkOrderTaskCompletionSerializer(serializers.ModelSerializer):
    """Serializer for task completion records within a work order.

    Carries both halves of the per-step photo pair, read-only: the template
    step's ``task_reference_image_url`` ("what this should look like", set once
    on the MaintenanceTask) and the ``evidence_photos`` a tech attached to this
    specific step while doing the work.

    ``elapsed_seconds`` is the step's stopwatch, reported LIVE — see
    :meth:`get_elapsed_seconds`.
    """

    completed_by_name = serializers.SerializerMethodField()
    task_reference_image_url = serializers.SerializerMethodField()
    evidence_photos = serializers.SerializerMethodField()
    elapsed_seconds = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderTaskCompletion
        fields = [
            "id",
            "work_order",
            "task",
            "task_title",
            "task_order",
            "is_required",
            "is_completed",
            "completed_by",
            "completed_by_name",
            "completed_at",
            "notes",
            "task_reference_image_url",
            "evidence_photos",
            "elapsed_seconds",
            "is_timing",
            "created_at",
        ]
        read_only_fields = [
            "created_at",
            "completed_by_name",
            "task_title",
            "task_order",
            "task_reference_image_url",
            "evidence_photos",
            "elapsed_seconds",
            "is_timing",
        ]

    def get_completed_by_name(self, obj):
        if obj.completed_by:
            return obj.completed_by.get_full_name() or obj.completed_by.username
        return None

    def get_task_reference_image_url(self, obj):
        """The template step's reference photo, or None.

        ``task`` is nullable (the step can be deleted after the WO is cut), so
        this degrades to None rather than raising. Reads through the
        ``task_completions__task`` select_related/prefetch the viewset sets up.
        """
        task = obj.task
        if task is None or not task.reference_image:
            return None
        request = self.context.get("request")
        if request is None:
            return None
        return request.build_absolute_uri(task.reference_image.url)

    def get_evidence_photos(self, obj):
        """Photos pinned to this step. Uses ``.all()`` so a prefetch applies."""
        return WorkOrderEvidencePhotoSerializer(
            obj.evidence_photos.all(), many=True, context=self.context
        ).data

    def get_elapsed_seconds(self, obj):
        """Whole seconds on this step's clock, INCLUDING a running segment.

        The stored column holds only committed time; while ``is_timing`` is true
        the segment in flight is added here. Clients tick a display over this
        value — they never own the total.
        """
        return obj.live_elapsed_seconds()


class WorkOrderMaterialUsageSerializer(serializers.ModelSerializer):
    """Serializer for material usage tracking within a work order.

    ``quantity_used`` and ``unit_cost`` are writable (the consumed amount that
    drives the inventory decrement, and the real price paid per unit);
    ``applied_quantity`` and ``stock_applied`` expose the decrement state
    read-only so the UI can show what was drawn from stock. ``actual_cost``
    (op-768w) is the line's real spend — ``quantity_used × unit_cost``, null
    when no cost was recorded.

    ``material_name``/``unit``/``inventory_item`` are read-only here: a
    template-derived row freezes them at generation, and an ad-hoc row sets
    them once through the ``add_material`` action. ``purchase_order_item``
    (op-bu80) is read-only too — it is set only by the receipt bridge, and a
    non-null value marks the line as mirroring a purchase-order line.
    """

    stock_applied = serializers.BooleanField(read_only=True)
    actual_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True, allow_null=True
    )
    inventory_item_name = serializers.SerializerMethodField()
    receipt_url = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderMaterialUsage
        fields = [
            "id",
            "work_order",
            "material",
            "material_name",
            "inventory_item",
            "inventory_item_name",
            "is_ad_hoc",
            "purchase_order_item",
            "quantity_planned",
            "quantity_used",
            "unit",
            "unit_cost",
            "actual_cost",
            "was_used",
            "applied_quantity",
            "stock_applied",
            "receipt_image",
            "receipt_url",
            "created_at",
        ]
        read_only_fields = [
            "created_at",
            "material_name",
            "quantity_planned",
            "unit",
            "applied_quantity",
            "inventory_item",
            "is_ad_hoc",
            "purchase_order_item",
            "receipt_url",
        ]

    def get_inventory_item_name(self, obj):
        """Name of the stock row this line draws from, for either kind of row.

        Reads :attr:`WorkOrderMaterialUsage.stock_item` so a template-derived
        line shows its spec's item and an ad-hoc line shows its direct link.
        """
        item = obj.stock_item
        return item.name if item is not None else None

    def get_receipt_url(self, obj):
        request = self.context.get("request")
        if obj.receipt_image and request:
            return request.build_absolute_uri(obj.receipt_image.url)
        return obj.receipt_image.url if obj.receipt_image else None


class WorkOrderAdHocMaterialSerializer(serializers.Serializer):
    """Write-only input for ``WorkOrderViewSet.add_material`` (op-768w).

    A separate input serializer rather than the model one because the model
    serializer deliberately freezes ``material_name``/``unit``/
    ``inventory_item`` — they are set exactly once, here. Going through a
    serializer (like ``add_photo`` does) is also what validates the uploaded
    ``receipt_image`` is a real image rather than trusting the content type.
    """

    material_name = serializers.CharField(max_length=200, trim_whitespace=True)
    quantity_used = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False
    )
    unit = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    unit_cost = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        allow_null=True,
    )
    inventory_item = serializers.PrimaryKeyRelatedField(
        queryset=InventoryItem.objects.all(), required=False, allow_null=True
    )
    receipt_image = serializers.ImageField(required=False, allow_null=True)


class WorkOrderToolSerializer(serializers.ModelSerializer):
    """A work order's own tool row — what to grab, and where it is for THIS job.

    Everything except ``location_hint`` is read-only: the display fields are
    frozen at generation (or set once through ``add_tool``), the same way
    ``WorkOrderMaterialUsageSerializer`` freezes ``material_name``/``unit``.
    ``location_hint`` stays writable because per-job restaging *is* the
    feature — editing it here never writes back to the PM template.

    ``resolved_location`` is what every surface displays; read it rather than
    ``location_hint``, which is blank whenever the linked inventory item's
    location is standing in.
    """

    resolved_location = serializers.CharField(read_only=True)
    inventory_item_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderTool
        fields = [
            "id",
            "work_order",
            "tool",
            "inventory_item",
            "inventory_item_name",
            "is_ad_hoc",
            "name",
            "quantity",
            "location_hint",
            "resolved_location",
            "is_required",
            "notes",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "work_order",
            "tool",
            "inventory_item",
            "inventory_item_name",
            "is_ad_hoc",
            "name",
            "quantity",
            "is_required",
            "notes",
            "created_at",
        ]

    def get_inventory_item_name(self, obj):
        """Name of the inventory item backing this tool, or ``None``."""
        return obj.inventory_item.name if obj.inventory_item_id else None


class WorkOrderAdHocToolSerializer(serializers.Serializer):
    """Write-only input for ``WorkOrderViewSet.add_tool`` (op-0v4).

    A separate input serializer rather than the model one, mirroring
    ``WorkOrderAdHocMaterialSerializer``: the model serializer freezes every
    display field, and they are set exactly once — here.

    ``quantity`` is bounded at 1 because "zero of a tool" is not a tool, and
    ``name`` is required and non-blank because it is the only thing the tech
    reads off the printed list.
    """

    name = serializers.CharField(max_length=200, trim_whitespace=True)
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)
    inventory_item = serializers.PrimaryKeyRelatedField(
        queryset=InventoryItem.objects.all(), required=False, allow_null=True
    )
    location_hint = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default=""
    )
    is_required = serializers.BooleanField(required=False, default=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class WorkOrderToolLocationSerializer(serializers.Serializer):
    """Write-only input for restaging a tool on one job (op-0v4).

    The whole editable surface of a work-order tool: where it is for THIS job.
    Blank is meaningful — it clears the per-job hint and lets the linked
    inventory item's location stand in again.
    """

    location_hint = serializers.CharField(max_length=200, allow_blank=True)


class WorkOrderLotoCompletionSerializer(serializers.ModelSerializer):
    """Serializer for per-energy-source LOTO completion within a work order.

    The denormalized descriptive fields (``source_type``/``source_label``/
    ``isolation_point``/``required_devices``) and ``energy_source`` are read-only
    — they are fixed at WO generation. Only ``is_completed`` / ``notes`` are
    writable (via the ``complete_loto`` action)."""

    completed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderLotoCompletion
        fields = [
            "id",
            "work_order",
            "energy_source",
            "source_type",
            "source_label",
            "isolation_point",
            "required_devices",
            "is_completed",
            "completed_by",
            "completed_by_name",
            "completed_at",
            "notes",
            "created_at",
        ]
        read_only_fields = [
            "created_at",
            "completed_by_name",
            "energy_source",
            "source_type",
            "source_label",
            "isolation_point",
            "required_devices",
        ]

    def get_completed_by_name(self, obj):
        if obj.completed_by:
            return obj.completed_by.get_full_name() or obj.completed_by.username
        return None


class WorkOrderPhotoSerializer(serializers.ModelSerializer):
    """Serializer for photos attached to a work order.

    ``task_completion`` is writable so a photo can be pinned to one step
    (evidence). It is null for work-order-level photos. The ``add_photo``
    action is what enforces that the step belongs to the same work order.
    """

    uploaded_by_name = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderPhoto
        fields = [
            "id",
            "work_order",
            "task_completion",
            "image",
            "image_url",
            "caption",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
        ]
        read_only_fields = ["uploaded_at", "uploaded_by_name", "image_url"]

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class WorkOrderAttachmentSerializer(serializers.ModelSerializer):
    """Serializer for a file attached to a standard (internal) work order.

    Mirrors ``PurchaseOrderAttachmentSerializer`` — ``file`` is the multipart
    write field and ``file_url`` / ``file_name`` are what a client renders, so
    the ScanTTY and web attachment lists never have to build a media URL
    themselves. ``work_order`` is writable because attachments are created
    through the top-level ``work-order-attachments/`` route rather than a
    nested action.
    """

    uploaded_by_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    file_name = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = WorkOrderAttachment
        fields = [
            "id",
            "work_order",
            "file",
            "file_url",
            "file_name",
            "kind",
            "kind_display",
            "description",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
        ]
        read_only_fields = [
            "id",
            "uploaded_at",
            # Server-stamped from request.user in the viewset — a client must
            # not be able to attribute an upload to somebody else.
            "uploaded_by",
            "uploaded_by_name",
            "file_url",
            "file_name",
            "kind_display",
        ]

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None

    def get_file_url(self, obj):
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        if obj.file:
            return obj.file.url
        return None

    def get_file_name(self, obj):
        if obj.file:
            return obj.file.name.rsplit("/", 1)[-1]
        return None


class WorkOrderSubmissionSerializer(serializers.ModelSerializer):
    """Serializer for an inbound (emailed or manually-uploaded) WO submission."""

    pdf_url = serializers.SerializerMethodField()
    submitted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderSubmission
        fields = [
            "id",
            "pdf_url",
            "received_at",
            "status",
            "source",
            "from_email",
            "subject",
            "submitted_by",
            "submitted_by_name",
            "parse_error",
            "pending_changes",
        ]
        read_only_fields = fields

    def get_pdf_url(self, obj):
        if not obj.attachment:
            return None
        request = self.context.get("request")
        url = obj.attachment.url
        return request.build_absolute_uri(url) if request else url

    def get_submitted_by_name(self, obj):
        if obj.submitted_by:
            return obj.submitted_by.get_full_name() or obj.submitted_by.username
        return None


class WorkOrderValidationSerializer(serializers.ModelSerializer):
    """AC-3 audit trail of pre-finalization validation acknowledgements."""

    validated_by_name = serializers.SerializerMethodField()
    is_complete = serializers.ReadOnlyField()

    class Meta:
        model = WorkOrderValidation
        fields = [
            "id",
            "work_order",
            "validated_by",
            "validated_by_name",
            "validated_at",
            "electrical_acknowledged",
            "loto_acknowledged",
            "required_fields_acknowledged",
            "is_complete",
            "notes",
        ]
        read_only_fields = [
            "id",
            "work_order",
            "validated_by",
            "validated_by_name",
            "validated_at",
            "is_complete",
        ]

    def get_validated_by_name(self, obj):
        if obj.validated_by:
            return obj.validated_by.get_full_name() or obj.validated_by.username
        return None


def _pending_review_count(work_order) -> int:
    """Count a WO's PENDING_REVIEW submissions from the prefetched cache.

    Reads ``work_order.submissions.all()`` with no filter/order override so it
    hits the ``prefetch_related("submissions")`` cache on the list/detail
    queryset instead of firing a query per row (N+1). Drives the "N scanned
    marks to review" badge on the WO list rows + detail header (op-o6rs).
    """
    return sum(
        1
        for s in work_order.submissions.all()
        if s.status == WorkOrderSubmission.Status.PENDING_REVIEW
    )


class WorkOrderSerializer(serializers.ModelSerializer):
    """Full serializer for a work order, including nested completions and photos."""

    # Asset fields read the work order's own FK, not the PM template's: a
    # corrective work order has no template and would otherwise lose its asset.
    maintenance_item_title = serializers.SerializerMethodField()
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    asset_id = serializers.UUIDField(source="asset.id", read_only=True)
    # The template's guess, carried alongside the stopwatch so the UI (and the
    # printed sign-off) can put actual next to estimate without a second fetch.
    # Null for corrective work: nothing estimated it.
    estimated_time_minutes = serializers.SerializerMethodField()
    display_title = serializers.ReadOnlyField()
    assigned_to_name = serializers.SerializerMethodField()
    short_id = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    elapsed_seconds = serializers.SerializerMethodField()
    task_completions = WorkOrderTaskCompletionSerializer(many=True, read_only=True)
    material_usage = WorkOrderMaterialUsageSerializer(many=True, read_only=True)
    # op-768w/op-4pzp: real money spent on materials — every priced *used* line
    # plus every priced *ad-hoc* one, whose cost is already spent whether or not
    # anyone marks it used. Rides the ``material_usage`` prefetch, no extra query.
    actual_material_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    loto_completions = WorkOrderLotoCompletionSerializer(many=True, read_only=True)
    photos = WorkOrderPhotoSerializer(many=True, read_only=True)
    tools = serializers.SerializerMethodField()
    # op-0v4: the work order's OWN tool rows, in full. Named ``tool_rows``
    # rather than ``tools`` because ``tools`` is the pinned ScanTTY display
    # payload and its key set cannot grow. This is the editable surface — which
    # rows exist, which are ad-hoc (so removable), and their per-job location.
    # Empty on a work order generated before per-job tools, whose ``tools``
    # falls back to the PM template.
    tool_rows = WorkOrderToolSerializer(source="tools", many=True, read_only=True)
    submissions = serializers.SerializerMethodField()
    pending_review_count = serializers.SerializerMethodField()
    has_pending_review = serializers.SerializerMethodField()
    electrical = serializers.SerializerMethodField()
    loto = serializers.SerializerMethodField()
    validation = serializers.SerializerMethodField()
    reference_documents = serializers.SerializerMethodField()
    # op-bu80: the PO lines ordered to complete this job — on order *and*
    # received — so the job page can say "the part is still in transit".
    purchase_order_lines = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "short_id",
            "maintenance_item",
            "maintenance_item_title",
            "display_title",
            "asset",
            "asset_name",
            "asset_tag",
            "asset_id",
            "estimated_time_minutes",
            "status",
            "due_date",
            "assigned_to",
            "assigned_to_name",
            "completed_by_name",
            "started_at",
            "completed_at",
            "elapsed_seconds",
            "is_timing",
            "notes",
            "loto_completion_note",
            "is_overdue",
            "task_completions",
            "material_usage",
            "actual_material_cost",
            "purchase_order_lines",
            "loto_completions",
            "photos",
            "tools",
            "tool_rows",
            "submissions",
            "pending_review_count",
            "has_pending_review",
            "electrical",
            "loto",
            "validation",
            "reference_documents",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "short_id",
            "display_title",
            "is_overdue",
            "created_at",
            "updated_at",
            "started_at",
            "elapsed_seconds",
            "is_timing",
            "task_completions",
            "material_usage",
            "actual_material_cost",
            "purchase_order_lines",
            "loto_completions",
            "photos",
            "tools",
            "tool_rows",
            "submissions",
            "pending_review_count",
            "has_pending_review",
            "electrical",
            "loto",
            "validation",
            "reference_documents",
        ]

    def validate(self, attrs):
        """A work order is always *for* something — a PM template or an asset.

        ``maintenance_item`` became nullable so corrective work orders can
        exist; without this a POST that names neither would quietly create a
        work order attached to nothing, which nothing downstream can render.
        """
        if self.instance is None:
            item = attrs.get("maintenance_item")
            asset = attrs.get("asset")
            if item is None and asset is None:
                raise serializers.ValidationError(
                    "Provide either a maintenance_item (preventive) or an asset (corrective)."
                )
        return attrs

    def get_maintenance_item_title(self, obj):
        """The PM template's title, or None for corrective work.

        Not ``display_title``: this key means "which template", and a caller
        distinguishing preventive from corrective work reads it as such.
        """
        return obj.maintenance_item.title if obj.maintenance_item_id else None

    def get_estimated_time_minutes(self, obj):
        return obj.maintenance_item.estimated_time_minutes if obj.maintenance_item_id else None

    def get_pending_review_count(self, obj):
        return _pending_review_count(obj)

    def get_has_pending_review(self, obj):
        return _pending_review_count(obj) > 0

    def get_submissions(self, obj):
        qs = obj.submissions.all().order_by("-received_at")
        return WorkOrderSubmissionSerializer(qs, many=True, context=self.context).data

    def get_assigned_to_name(self, obj):
        if obj.assigned_to:
            return obj.assigned_to.get_full_name() or obj.assigned_to.username
        return None

    def get_elapsed_seconds(self, obj):
        """Whole seconds on the work-order clock, INCLUDING a running segment.

        The stored column holds only committed time; while ``is_timing`` is true
        the segment in flight is added here, so a client that just re-fetches
        (or reloads) sees the true running total without doing arithmetic on
        ``timing_since``. Compare against ``estimated_time_minutes``.
        """
        return obj.live_elapsed_seconds()

    def get_tools(self, obj):
        """Tools the tech needs to gather before starting, required ones first.

        A flat reference list (no OMR checkbox — nothing here is scanned back)
        carried on every work order so the web detail page can show "what to
        grab" up front. Built by the same helper the printed form uses, so the
        two surfaces cannot drift.

        op-0v4: the work order's own tool rows when it has any — including a
        corrective one's, which have no template to come from — otherwise the
        PM template's, which is what every work order generated before per-job
        tools shows. ``location_hint`` carries the resolved per-job location.
        """
        from .services.work_order_context import build_tools_context

        return build_tools_context(obj)

    def get_electrical(self, obj):
        from .services.work_order_context import build_electrical_context

        return build_electrical_context(obj.asset)

    def get_loto(self, obj):
        from .services.work_order_context import build_loto_context

        return build_loto_context(obj.asset)

    def get_validation(self, obj):
        latest = obj.validations.order_by("-validated_at").first()
        if latest is None:
            return None
        return WorkOrderValidationSerializer(latest, context=self.context).data

    def get_reference_documents(self, obj):
        """Manual / revision history / reference links for the sign-off surface.

        Read-only projection of the asset's existing document library — no new
        link fields on the work order. Detail-only: the list view has no room
        for it and would pay the prefetch for nothing.
        """
        from .services.work_order_context import build_reference_documents_context

        return build_reference_documents_context(
            obj.asset,
            request=self.context.get("request"),
        )

    def get_purchase_order_lines(self, obj):
        """PO lines ordered to complete this job — on order and received (op-bu80).

        The ordering-side view of the same fact the received material lines
        record: what was bought for this work order, from whom, and how much of
        it has actually shown up. Detail-only, like ``reference_documents``.
        """
        from .services.work_order_context import build_purchase_lines_context

        return build_purchase_lines_context(obj)


class WorkOrderListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for work order list views."""

    maintenance_item_title = serializers.SerializerMethodField()
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    asset_id = serializers.UUIDField(source="asset.id", read_only=True)
    display_title = serializers.ReadOnlyField()
    short_id = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    task_completion_count = serializers.SerializerMethodField()
    task_total_count = serializers.SerializerMethodField()
    pending_review_count = serializers.SerializerMethodField()
    has_pending_review = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "short_id",
            "maintenance_item",
            "maintenance_item_title",
            "display_title",
            "asset",
            "asset_name",
            "asset_tag",
            "asset_id",
            "status",
            "due_date",
            "is_overdue",
            "completed_by_name",
            "completed_at",
            "task_completion_count",
            "task_total_count",
            "pending_review_count",
            "has_pending_review",
            "created_at",
            "updated_at",
        ]

    def get_maintenance_item_title(self, obj):
        return obj.maintenance_item.title if obj.maintenance_item_id else None

    def get_task_completion_count(self, obj):
        return obj.task_completions.filter(is_completed=True).count()

    def get_task_total_count(self, obj):
        return obj.task_completions.count()

    def get_pending_review_count(self, obj):
        return _pending_review_count(obj)

    def get_has_pending_review(self, obj):
        return _pending_review_count(obj) > 0


class StockReconciliationSerializer(serializers.ModelSerializer):
    """Read serializer for StockReconciliation audit rows."""

    item_name = serializers.CharField(source="item.name", read_only=True)
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    reconciled_by_name = serializers.SerializerMethodField()
    triggered_reorder_id = serializers.PrimaryKeyRelatedField(
        source="triggered_reorder", read_only=True
    )

    class Meta:
        model = StockReconciliation
        fields = [
            "id",
            "item",
            "item_name",
            "item_sku",
            "projected_count",
            "actual_count",
            "delta",
            "reason",
            "notes",
            "reconciled_by",
            "reconciled_by_name",
            "reconciled_at",
            "triggered_reorder",
            "triggered_reorder_id",
        ]
        read_only_fields = fields

    def get_reconciled_by_name(self, obj):
        user = obj.reconciled_by
        if not user:
            return ""
        full = (getattr(user, "get_full_name", lambda: "")() or "").strip()
        return full or getattr(user, "username", "") or getattr(user, "email", "")


class StockReconciliationRowSerializer(serializers.Serializer):
    """A single row in a batch reconciliation submission.

    ``actual_count`` is base units, unchanged. The two optional fields (op-ev14)
    let a caller count the way the item is stocked instead:

    * ``at_level`` — read ``actual_count`` as whole packs of the item's
      ``count_level`` ("I counted 3 cases"). Rejected on an item that is not
      counted in packs, rather than silently read as base units.
    * ``open_count`` — sets the open-container tally, ``open_closed`` only.
    """

    item_id = serializers.UUIDField()
    actual_count = serializers.IntegerField(min_value=0)
    reason = serializers.ChoiceField(choices=StockReconciliation.ReasonCode.choices)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    skip_reorder = serializers.BooleanField(required=False, default=False)
    at_level = serializers.BooleanField(required=False, default=False)
    open_count = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, default=None
    )


class StockReconciliationBatchSerializer(serializers.Serializer):
    """Batch payload: a list of reconciliation rows."""

    rows = StockReconciliationRowSerializer(many=True, allow_empty=False)


class AssetTcoReportSerializer(serializers.Serializer):
    """One row in the per-asset Total Cost of Ownership report."""

    asset_id = serializers.UUIDField()
    asset_name = serializers.CharField()
    asset_tag = serializers.CharField(allow_blank=True)
    maintenance_days_last_90 = serializers.IntegerField()
    scheduled_maintenance_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    unscheduled_maintenance_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    repair_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    tco = serializers.DecimalField(max_digits=12, decimal_places=2)
    preventive_maintenance_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    vendor_maintenance_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_maintenance_cost_90d = serializers.DecimalField(max_digits=12, decimal_places=2)


class AssetCostRecoveryServiceSerializer(serializers.Serializer):
    """One itemized service line in the asset cost-recovery statement.

    Three cost columns, and the split between them is the point of the report:

    * ``estimated_cost`` — the internal estimate (present for internal PM, null
      for vendor/manual work that has no per-asset estimate).
    * ``internal_cost`` — what in-house work *really* cost: the captured
      material actuals where they exist, the estimate otherwise. Informational
      on every asset, recoverable on none by itself. Null for vendor/manual
      rows, which are not in-house work.
    * ``actual_cost`` — the landlord-billable column: the vendor invoice /
      recorded actual, plus in-house actuals **only on an asset flagged
      ``is_cost_recoverable``**.

    The recoverable amount is the sum of the ``actual_cost`` column.
    """

    date = serializers.DateField()
    source = serializers.ChoiceField(choices=["pm", "vendor", "manual"])
    description = serializers.CharField(allow_blank=True)
    estimated_cost = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    internal_cost = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    actual_cost = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)


class AssetCostRecoveryReportSerializer(serializers.Serializer):
    """One asset block in the cost-recovery statement: asset info + services.

    ``subtotal_actual`` is the recoverable amount for this asset; the report's
    ``grand_total_actual`` is the recoverable total billed to the landlord.
    ``subtotal_internal`` is the in-house spend on this asset, which only feeds
    the recoverable total when ``is_cost_recoverable`` is set.
    """

    asset_id = serializers.UUIDField()
    asset_tag = serializers.CharField(allow_blank=True)
    name = serializers.CharField()
    serial_number = serializers.CharField(allow_blank=True)
    date_received = serializers.DateField(allow_null=True)
    status = serializers.CharField()
    status_display = serializers.CharField()
    category = serializers.CharField(allow_null=True, allow_blank=True)
    is_cost_recoverable = serializers.BooleanField()
    services = AssetCostRecoveryServiceSerializer(many=True)
    subtotal_estimated = serializers.DecimalField(max_digits=12, decimal_places=2)
    subtotal_internal = serializers.DecimalField(max_digits=12, decimal_places=2)
    subtotal_actual = serializers.DecimalField(max_digits=12, decimal_places=2)


class LocationReconcileItemSerializer(serializers.ModelSerializer):
    """Single item row in the location reconcile grid payload.

    ``projected`` stays the canonical base-unit stock. ``count_mode`` /
    ``count_unit`` / ``projected_at_unit`` name the unit the counter should
    enter ``actual_count`` in (op-ev14) — the item's counting rung for a
    pack-counting item, its base unit otherwise — so the grid can label the
    input instead of leaving a case count to be multiplied out by hand.
    """

    item_id = serializers.UUIDField(source="id", read_only=True)
    projected = serializers.IntegerField(source="current_stock", read_only=True)
    owning_group_name = serializers.CharField(
        source="owning_group.name", read_only=True, default=""
    )
    count_unit = serializers.SerializerMethodField()
    projected_at_unit = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            "item_id",
            "name",
            "sku",
            "projected",
            "minimum_stock",
            "reorder_quantity",
            "owning_group_name",
            "count_mode",
            "count_unit",
            "projected_at_unit",
            "open_container_count",
        ]
        read_only_fields = fields

    def get_count_unit(self, obj) -> str:
        from inventory.services.packaging import count_unit

        return count_unit(obj)

    def get_projected_at_unit(self, obj) -> int:
        from inventory.services.packaging import count_at_level

        return count_at_level(obj)


class MaintenanceRecordSerializer(serializers.ModelSerializer):
    """Serializer for backdated/recent maintenance records on an asset."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default=None)
    performed_by_internal_username = serializers.CharField(
        source="performed_by_internal.username", read_only=True, default=None
    )
    recorded_by_username = serializers.CharField(
        source="recorded_by.username", read_only=True, default=None
    )
    attachment_url = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceRecord
        fields = [
            "id",
            "asset",
            "asset_name",
            "title",
            "description",
            "completed_on",
            "vendor",
            "vendor_name",
            "performed_by_internal",
            "performed_by_internal_username",
            "cost",
            "invoice_number",
            "attachment",
            "attachment_url",
            "notes",
            "recorded_by",
            "recorded_by_username",
            "recorded_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "recorded_by",
            "recorded_at",
            "updated_at",
        ]

    def get_attachment_url(self, obj):
        if not obj.attachment:
            return None
        request = self.context.get("request")
        url = obj.attachment.url
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    def validate(self, attrs):
        attrs = super().validate(attrs)
        vendor = attrs.get("vendor", getattr(self.instance, "vendor", None))
        internal = attrs.get(
            "performed_by_internal", getattr(self.instance, "performed_by_internal", None)
        )
        if vendor is None and internal is None:
            raise serializers.ValidationError(
                {
                    "performed_by_internal": (
                        "Either a vendor or an internal staff member must be set."
                    )
                }
            )
        completed_on = attrs.get("completed_on", getattr(self.instance, "completed_on", None))
        from django.utils import timezone as _tz

        if completed_on is not None and completed_on > _tz.localdate():
            raise serializers.ValidationError(
                {"completed_on": "completed_on cannot be in the future."}
            )
        return attrs


class AssetReservationSerializer(serializers.ModelSerializer):
    """Per-asset reservation for a class / training / event.

    `reserved_by` is read-only — the viewset injects request.user on
    create so the caller can't pin a reservation to someone else.
    `is_current` is computed server-side and surfaces "the e-paper
    panel will be showing this RIGHT NOW" to clients.
    """

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    reserved_by_username = serializers.CharField(source="reserved_by.username", read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = AssetReservation
        fields = [
            "id",
            "asset",
            "asset_name",
            "title",
            "reserved_by",
            "reserved_by_username",
            "starts_at",
            "ends_at",
            "notes",
            "cancelled_at",
            "is_active",
            "is_current",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reserved_by",
            "reserved_by_username",
            "asset_name",
            "is_active",
            "is_current",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]


class AssetOutOfServiceSerializer(serializers.ModelSerializer):
    """OOS event against an asset. POST opens, /restore/ closes."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    placed_by_username = serializers.CharField(source="placed_by.username", read_only=True)
    restored_by_username = serializers.CharField(
        source="restored_by.username", read_only=True, allow_null=True
    )
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = AssetOutOfService
        fields = [
            "id",
            "asset",
            "asset_name",
            "placed_out_at",
            "placed_by",
            "placed_by_username",
            "expected_return_at",
            "reason",
            "restored_at",
            "restored_by",
            "restored_by_username",
            "is_open",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "asset_name",
            "placed_out_at",
            "placed_by",
            "placed_by_username",
            "restored_at",
            "restored_by",
            "restored_by_username",
            "is_open",
            "created_at",
            "updated_at",
        ]


class ComponentUsageEventSerializer(serializers.ModelSerializer):
    """Read-only serializer for a serialized component's usage/audit log."""

    action_display = serializers.CharField(source="get_action_display", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True, default=None)
    actor_username = serializers.CharField(source="actor.username", read_only=True, default=None)
    # Denormalized so a log filtered by ?asset= (the serials a machine has used)
    # is self-describing without a follow-up fetch per component.
    component_serial = serializers.CharField(source="component.serial_number", read_only=True)
    component_item_name = serializers.CharField(source="component.item.name", read_only=True)

    class Meta:
        model = ComponentUsageEvent
        fields = [
            "id",
            "component",
            "component_serial",
            "component_item_name",
            "asset",
            "asset_name",
            "action",
            "action_display",
            "at",
            "actor",
            "actor_username",
            "notes",
            "created_at",
        ]
        # Events are created as a side effect of SerializedComponent lifecycle
        # actions; the API surface is read-only.
        read_only_fields = fields


class SerializedComponentSerializer(serializers.ModelSerializer):
    """Serializer for individual serial-numbered component units."""

    item_name = serializers.CharField(source="item.name", read_only=True)
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    tracking_mode = serializers.CharField(read_only=True)
    available_actions = serializers.ListField(child=serializers.CharField(), read_only=True)
    installed_in_asset_name = serializers.CharField(
        source="installed_in_asset.name", read_only=True, default=None
    )

    class Meta:
        model = SerializedComponent
        fields = [
            "id",
            "item",
            "item_name",
            "item_sku",
            "serial_number",
            "lot",
            "expiration_date",
            "status",
            "status_display",
            "tracking_mode",
            "available_actions",
            "installed_in_asset",
            "installed_in_asset_name",
            "received_at",
            "installed_at",
            "disposed_at",
            "provenance_delivery_item",
            "provenance_purchase_order_item",
            "disposal_reason",
            "created_at",
            "updated_at",
        ]
        # status and lifecycle timestamps/reason are driven by the lifecycle
        # action endpoints, not by direct writes; installed_in_asset is set via
        # the install/remove actions.
        read_only_fields = [
            "id",
            "status",
            "installed_in_asset",
            "received_at",
            "installed_at",
            "disposed_at",
            "disposal_reason",
            "created_at",
            "updated_at",
        ]

    def validate_item(self, value: InventoryItem) -> InventoryItem:
        if not value.is_serialized:
            raise serializers.ValidationError(
                "Inventory item must have is_serialized=True to track serialized components."
            )
        return value
