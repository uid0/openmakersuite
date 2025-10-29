"""
Factory classes for generating test data for inventory models.
"""

import itertools
from io import BytesIO

import factory
from django.core.files.uploadedfile import SimpleUploadedFile
from factory import Faker, SubFactory
from factory.django import DjangoModelFactory
from faker import Faker as FakerGenerator
from inventory.models import Category, InventoryItem, ItemSupplier, Location, Supplier, UsageLog
from PIL import Image


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
    supplier_type = factory.Iterator([Supplier.LOCAL, Supplier.ONLINE, Supplier.NATIONAL])
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
            name=f"{self.name}_image.jpg", content=img_io.read(), content_type="image/jpeg"
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
