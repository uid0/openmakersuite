from django.urls import reverse

import pytest

from inventory.models import ItemSupplier
from inventory.tests.factories import SupplierFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("supplied_lead_time", "expected_days", "expected_provenance"),
    [
        ("soon", 7, ItemSupplier.LeadTimeProvenance.DEFAULT),
        ("", 7, ItemSupplier.LeadTimeProvenance.DEFAULT),
        (0, 0, ItemSupplier.LeadTimeProvenance.RECORDED),
        (12, 12, ItemSupplier.LeadTimeProvenance.RECORDED),
    ],
)
def test_item_create_keeps_parsed_lead_time_and_provenance_together(
    authenticated_client,
    supplied_lead_time,
    expected_days,
    expected_provenance,
):
    client, _ = authenticated_client
    supplier = SupplierFactory()

    response = client.post(
        reverse("inventoryitem-list"),
        {
            "name": f"Lead time {supplied_lead_time!r}",
            "sku": f"LEAD-{expected_days}-{expected_provenance}-{supplier.pk}",
            "description": "Lead-time provenance test item",
            "reorder_quantity": 1,
            "supplier": supplier.pk,
            "average_lead_time": supplied_lead_time,
        },
        format="json",
    )

    assert response.status_code == 201
    link = ItemSupplier.objects.get(item_id=response.data["id"], supplier=supplier)
    assert link.average_lead_time == expected_days
    assert link.average_lead_time_provenance == expected_provenance
