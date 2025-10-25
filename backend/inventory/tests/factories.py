"""
Factory classes for generating test data for inventory models.
"""
import factory
from factory.django import DjangoModelFactory
from factory import Faker, SubFactory
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from io import BytesIO

from inventory.models import Supplier, Category, InventoryItem, UsageLog


class SupplierFactory(DjangoModelFactory):
    """Factory for creating Supplier instances."""

    class Meta:
        model = Supplier

    name = Faker('company')
    supplier_type = factory.Iterator([
        Supplier.LOCAL,
        Supplier.ONLINE,
        Supplier.NATIONAL
    ])
    website = Faker('url')
    notes = Faker('text', max_nb_chars=200)


class CategoryFactory(DjangoModelFactory):
    """Factory for creating Category instances."""

    class Meta:
        model = Category

    name = Faker('word')
    slug = factory.LazyAttribute(lambda obj: obj.name.lower().replace(' ', '-'))
    description = Faker('text', max_nb_chars=200)
    parent = None


class InventoryItemFactory(DjangoModelFactory):
    """Factory for creating InventoryItem instances."""

    class Meta:
        model = InventoryItem

    name = Faker('word')
    description = Faker('text', max_nb_chars=200)
    sku = factory.Sequence(lambda n: f'SKU-{n:05d}')
    location = Faker('city')
    reorder_quantity = Faker('random_int', min=1, max=50)
    current_stock = Faker('random_int', min=0, max=100)
    minimum_stock = Faker('random_int', min=1, max=20)
    supplier = SubFactory(SupplierFactory)
    supplier_sku = factory.Sequence(lambda n: f'SUP-SKU-{n:05d}')
    supplier_url = Faker('url')
    unit_cost = Faker('pydecimal', left_digits=3, right_digits=2, positive=True)
    average_lead_time = Faker('random_int', min=1, max=30)
    is_active = True
    notes = Faker('text', max_nb_chars=100)

    @factory.lazy_attribute
    def image(self):
        """Generate a simple test image."""
        img = Image.new('RGB', (100, 100), color='blue')
        img_io = BytesIO()
        img.save(img_io, format='JPEG')
        img_io.seek(0)
        return SimpleUploadedFile(
            name=f'{self.name}_image.jpg',
            content=img_io.read(),
            content_type='image/jpeg'
        )

    @factory.post_generation
    def category(self, create, extracted, **kwargs):
        """Add category if provided."""
        if not create:
            return
        if extracted:
            self.category = extracted


class UsageLogFactory(DjangoModelFactory):
    """Factory for creating UsageLog instances."""

    class Meta:
        model = UsageLog

    item = SubFactory(InventoryItemFactory)
    quantity_used = Faker('random_int', min=1, max=10)
    notes = Faker('text', max_nb_chars=100)
