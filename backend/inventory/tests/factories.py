"""
Factory classes for generating test data for inventory models.
"""

import itertools
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile

import factory
from factory import Faker, SubFactory
from factory.django import DjangoModelFactory
from faker import Faker as FakerGenerator
from PIL import Image

from inventory.models import (
    Asset,
    AssetPart,
    AssetProblem,
    Category,
    Fixture,
    InventoryItem,
    ItemSupplier,
    Location,
    SerializedComponent,
    Supplier,
    UsageLog,
)


class LocationFactory(DjangoModelFactory):
    """Factory for creating Location instances."""

    class Meta:
        model = Location

    name = factory.Sequence(lambda n: f"Location {n}")
    description = Faker("sentence")
    is_active = True


class SupplierFactory(DjangoModelFactory):
    """Factory for creating Supplier instances."""

    class Meta:
        model = Supplier

    name = factory.Sequence(lambda n: f"supplier-{n}")
    supplier_type = factory.Iterator(
        [Supplier.SupplierType.LOCAL, Supplier.SupplierType.ONLINE, Supplier.SupplierType.NATIONAL]
    )
    website = Faker("url")
    notes = Faker("text", max_nb_chars=200)


class CategoryFactory(DjangoModelFactory):
    """Factory for creating Category instances."""

    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"category-{n}")
    slug = factory.LazyAttribute(lambda obj: obj.name.lower().replace(" ", "-"))
    description = Faker("text", max_nb_chars=200)
    parent = None


class InventoryItemFactory(DjangoModelFactory):
    """Factory for creating InventoryItem instances."""

    class Meta:
        model = InventoryItem

    name = factory.Sequence(lambda n: f"item-{n}")
    description = Faker("text", max_nb_chars=200)
    sku = factory.Sequence(lambda n: f"SKU-{n:05d}")
    location = SubFactory(LocationFactory)
    reorder_quantity = Faker("random_int", min=1, max=50)
    current_stock = Faker("random_int", min=0, max=100)
    minimum_stock = Faker("random_int", min=1, max=20)
    is_active = True
    notes = Faker("text", max_nb_chars=100)

    _supplier_sku_sequence = itertools.count()
    _faker = FakerGenerator()

    @factory.lazy_attribute
    def image(self):
        """Generate a simple test image."""
        img = Image.new("RGB", (100, 100), color="blue")
        img_io = BytesIO()
        img.save(img_io, format="JPEG")
        img_io.seek(0)
        return SimpleUploadedFile(
            name=f"{self.name}_image.jpg",
            content=img_io.read(),
            content_type="image/jpeg",
        )

    @factory.post_generation
    def category(self, create, extracted, **kwargs):
        """Add category if provided."""
        if not create:
            return
        if extracted:
            self.category = extracted

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        location_provided = "location" in kwargs
        location_value = kwargs.pop("location", None)
        supplier = kwargs.pop("supplier", None)
        supplier_sku = kwargs.pop("supplier_sku", None)
        supplier_url = kwargs.pop("supplier_url", None)
        unit_cost_specified = "unit_cost" in kwargs
        unit_cost = kwargs.pop("unit_cost", None)
        average_lead_time = kwargs.pop("average_lead_time", None)
        quantity_per_package = kwargs.pop("quantity_per_package", 1)
        package_upc = kwargs.pop("package_upc", "")
        unit_upc = kwargs.pop("unit_upc", "")
        is_primary = kwargs.pop("is_primary", True)
        item_supplier_kwargs = kwargs.pop("item_supplier_kwargs", {})

        if location_value is None:
            location = None if location_provided else LocationFactory()
        elif isinstance(location_value, Location):
            location = location_value
        else:
            try:
                location = Location.objects.get(pk=location_value)
            except (Location.DoesNotExist, ValueError, TypeError):
                location, _ = Location.objects.get_or_create(name=str(location_value))

        kwargs["location"] = location

        item = super()._create(model_class, *args, **kwargs)

        if supplier is None:
            supplier = SupplierFactory()

        if supplier_sku is None:
            supplier_sku = f"SUP-SKU-{next(cls._supplier_sku_sequence):05d}"

        if supplier_url is None:
            supplier_url = cls._faker.url()

        if not unit_cost_specified:
            unit_cost = cls._faker.pydecimal(left_digits=3, right_digits=2, positive=True)

        if average_lead_time is None:
            average_lead_time = cls._faker.random_int(min=1, max=30)

        ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku=supplier_sku,
            supplier_url=supplier_url,
            unit_cost=unit_cost,
            average_lead_time=average_lead_time,
            quantity_per_package=quantity_per_package,
            package_upc=package_upc,
            unit_upc=unit_upc,
            is_primary=is_primary,
            **item_supplier_kwargs,
        )

        return item


class ItemSupplierFactory(DjangoModelFactory):
    """Factory for creating ItemSupplier instances."""

    class Meta:
        model = ItemSupplier

    item = SubFactory(InventoryItemFactory)
    supplier = SubFactory(SupplierFactory)
    supplier_sku = factory.Sequence(lambda n: f"SUP-SKU-{n:05d}")
    supplier_url = Faker("url")
    package_upc = factory.Sequence(lambda n: f"PKG-{n:012d}")
    unit_upc = factory.Sequence(lambda n: f"UNIT-{n:012d}")
    quantity_per_package = Faker("random_int", min=1, max=50)

    # Dimensional fields (can be overridden in tests)
    package_height = None
    package_width = None
    package_length = None
    package_weight = None

    # Pricing fields
    unit_cost = Faker("pydecimal", left_digits=3, right_digits=4, positive=True)
    package_cost = Faker("pydecimal", left_digits=3, right_digits=2, positive=True)
    average_lead_time = Faker("random_int", min=1, max=30)

    is_primary = True
    is_active = True
    notes = Faker("text", max_nb_chars=200)


class UsageLogFactory(DjangoModelFactory):
    """Factory for creating UsageLog instances."""

    class Meta:
        model = UsageLog

    item = SubFactory(InventoryItemFactory)
    quantity_used = Faker("random_int", min=1, max=10)
    notes = Faker("text", max_nb_chars=100)


class FixtureFactory(DjangoModelFactory):
    """Factory for creating Fixture instances."""

    class Meta:
        model = Fixture

    name = factory.Sequence(lambda n: f"Fixture {n}")
    description = Faker("sentence")
    location = SubFactory(LocationFactory)
    refill_item = SubFactory(InventoryItemFactory)
    asset_tag = factory.Sequence(lambda n: f"FIX-{n:05d}")
    is_active = True


class AssetFactory(DjangoModelFactory):
    """Factory for creating Asset instances.

    The operational/site-requirements fields (``breaker``, ``disconnect``,
    ``needs_compressed_air``, ``needs_ventilation``,
    ``generates_heat_or_flame``, ``needs_chilling``, ``special_requirements``,
    ``work_safety_notes``) moved to the 1:1 ``facilities.AssetSiteRequirements``
    profile in #880. They are still accepted as ``AssetFactory(breaker=...)``
    kwargs for back-compat and routed into that profile after the asset is
    saved (a profile row is only created when a non-default is requested).
    """

    class Meta:
        model = Asset

    name = factory.Sequence(lambda n: f"Asset {n}")
    description = Faker("text", max_nb_chars=200)
    serial_number = factory.Sequence(lambda n: f"SER-{n:05d}")
    asset_tag = factory.Sequence(lambda n: f"AST-{n:05d}")
    category = SubFactory(CategoryFactory)
    location = SubFactory(LocationFactory)
    manufacturer = SubFactory(SupplierFactory)
    status = Asset.Status.ACTIVE
    is_active = True
    is_chargeable = False

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        profile_fields = (
            "breaker",
            "disconnect",
            "needs_compressed_air",
            "needs_ventilation",
            "generates_heat_or_flame",
            "needs_chilling",
            "special_requirements",
            "work_safety_notes",
        )
        profile_data = {key: kwargs.pop(key) for key in profile_fields if key in kwargs}
        asset = super()._create(model_class, *args, **kwargs)

        # Only materialise a profile row when a non-default was requested so
        # assets with no site requirements stay row-free (matching production).
        meaningful = {
            key: value for key, value in profile_data.items() if value not in (None, False, "")
        }
        if meaningful:
            from facilities.models import AssetSiteRequirements

            profile, _ = AssetSiteRequirements.objects.update_or_create(
                asset=asset, defaults=meaningful
            )
            asset.site_requirements = profile
            # Re-fire post_save so signal-driven derivations (loto) run with the
            # breaker now resolvable — matching the old direct-FK create path.
            asset.save()
        return asset


class AssetPartFactory(DjangoModelFactory):
    """Factory for creating AssetPart instances."""

    class Meta:
        model = AssetPart

    asset = SubFactory(AssetFactory)
    part = SubFactory(InventoryItemFactory)
    quantity_needed = Faker("random_int", min=1, max=5)
    is_required = True
    maintenance_interval_days = Faker("random_int", min=30, max=365)
    notes = Faker("text", max_nb_chars=100)


class AssetProblemFactory(DjangoModelFactory):
    """Factory for creating AssetProblem instances."""

    class Meta:
        model = AssetProblem

    asset = SubFactory(AssetFactory)
    part = None  # Optional, can be set explicitly in tests
    reported_by = Faker("user_name")
    description = Faker("text", max_nb_chars=200)
    status = AssetProblem.Status.REPORTED
    resolution_notes = ""


class SerializedComponentFactory(DjangoModelFactory):
    """Factory for SerializedComponent instances (consumable-mode by default).

    Pass ``item__serial_tracking_mode=InventoryItem.SerialTrackingMode.REUSABLE``
    to build a reusable-mode component.
    """

    class Meta:
        model = SerializedComponent

    item = SubFactory(
        InventoryItemFactory,
        is_serialized=True,
        serial_tracking_mode=InventoryItem.SerialTrackingMode.CONSUMABLE,
    )
    serial_number = factory.Sequence(lambda n: f"SN-{n:06d}")
    lot = ""
    status = SerializedComponent.Status.RECEIVED
