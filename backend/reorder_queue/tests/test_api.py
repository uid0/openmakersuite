"""
API tests for reorder queue endpoints.
"""
import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from datetime import timedelta

from reorder_queue.models import ReorderRequest
from reorder_queue.tests.factories import ReorderRequestFactory, UserFactory
from inventory.tests.factories import InventoryItemFactory, SupplierFactory


@pytest.mark.integration
class TestReorderRequestAPI:
    """Tests for ReorderRequest API endpoints."""

    def test_list_reorder_requests(self, authenticated_client):
        """Test listing reorder requests."""
        client, user = authenticated_client
        ReorderRequestFactory.create_batch(3)

        url = reverse('reorderrequest-list')
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) == 3

    def test_create_reorder_request_public(self, api_client):
        """Test anyone can create a reorder request."""
        item = InventoryItemFactory()

        url = reverse('reorderrequest-list')
        data = {
            'item': str(item.id),
            'quantity': 25,
            'requested_by': 'Jane Doe',
            'request_notes': 'We are running low',
            'priority': 'high'
        }
        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert str(response.data['item']) == str(item.id)
        assert response.data['quantity'] == 25
        assert response.data['status'] == 'pending'

    def test_create_reorder_request_minimal(self, api_client):
        """Test creating request with minimal required fields."""
        item = InventoryItemFactory()

        url = reverse('reorderrequest-list')
        data = {
            'item': str(item.id),
            'quantity': 10
        }
        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_201_CREATED

    def test_retrieve_reorder_request(self, authenticated_client):
        """Test retrieving a single reorder request."""
        client, user = authenticated_client
        request_obj = ReorderRequestFactory()

        url = reverse('reorderrequest-detail', kwargs={'pk': request_obj.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == request_obj.id
        assert 'item_details' in response.data

    def test_update_requires_auth(self, api_client):
        """Test updating request requires authentication."""
        request_obj = ReorderRequestFactory()

        url = reverse('reorderrequest-detail', kwargs={'pk': request_obj.pk})
        data = {'status': 'approved'}
        response = api_client.patch(url, data, format='json')

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_pending_requests_endpoint(self, authenticated_client):
        """Test getting pending requests only."""
        client, user = authenticated_client

        # Create requests with different statuses
        ReorderRequestFactory(status='pending')
        ReorderRequestFactory(status='pending')
        ReorderRequestFactory(status='approved')
        ReorderRequestFactory(status='cancelled')

        url = reverse('reorderrequest-pending')
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['count'] == 2
        assert len(response.data['results']) == 2
        for req in response.data['results']:
            assert req['status'] == 'pending'

    def test_by_supplier_endpoint(self, authenticated_client):
        """Test grouping requests by supplier."""
        client, user = authenticated_client

        supplier1 = SupplierFactory(name='Supplier A', supplier_type='amazon')
        supplier2 = SupplierFactory(name='Supplier B', supplier_type='grainger')

        item1 = InventoryItemFactory(supplier=supplier1)
        item2 = InventoryItemFactory(supplier=supplier1)
        item3 = InventoryItemFactory(supplier=supplier2)

        ReorderRequestFactory(item=item1, status='pending')
        ReorderRequestFactory(item=item2, status='pending')
        ReorderRequestFactory(item=item3, status='pending')

        url = reverse('reorderrequest-by-supplier')
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        # Check grouping
        supplier_names = [group['supplier'] for group in response.data]
        assert 'Supplier A' in supplier_names
        assert 'Supplier B' in supplier_names

    def test_approve_request(self, authenticated_client):
        """Test approving a reorder request."""
        client, user = authenticated_client
        request_obj = ReorderRequestFactory(status='pending')

        url = reverse('reorderrequest-approve', kwargs={'pk': request_obj.pk})
        data = {'admin_notes': 'Approved for ordering'}
        response = client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'approved'
        assert response.data['reviewed_by'] == user.id
        assert response.data['admin_notes'] == 'Approved for ordering'

        # Verify in database
        request_obj.refresh_from_db()
        assert request_obj.status == 'approved'
        assert request_obj.reviewed_by == user
        assert request_obj.reviewed_at is not None

    def test_mark_ordered(self, authenticated_client):
        """Test marking a request as ordered."""
        client, user = authenticated_client
        request_obj = ReorderRequestFactory(status='approved')

        url = reverse('reorderrequest-mark-ordered', kwargs={'pk': request_obj.pk})
        data = {
            'order_number': 'ORD-12345',
            'estimated_delivery': (timezone.now() + timedelta(days=7)).date().isoformat(),
            'actual_cost': '125.50'
        }
        response = client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'ordered'
        assert response.data['order_number'] == 'ORD-12345'

        # Verify in database
        request_obj.refresh_from_db()
        assert request_obj.status == 'ordered'
        assert request_obj.ordered_at is not None
        assert request_obj.order_number == 'ORD-12345'

    def test_mark_received(self, authenticated_client):
        """Test marking a request as received and updating inventory."""
        client, user = authenticated_client
        item = InventoryItemFactory(current_stock=10)
        request_obj = ReorderRequestFactory(
            item=item,
            quantity=50,
            status='ordered'
        )

        url = reverse('reorderrequest-mark-received', kwargs={'pk': request_obj.pk})
        response = client.post(url, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'received'

        # Verify inventory was updated
        item.refresh_from_db()
        assert item.current_stock == 60  # 10 + 50

        # Verify in database
        request_obj.refresh_from_db()
        assert request_obj.status == 'received'
        assert request_obj.actual_delivery is not None

    def test_cancel_request(self, authenticated_client):
        """Test cancelling a reorder request."""
        client, user = authenticated_client
        request_obj = ReorderRequestFactory(status='pending')

        url = reverse('reorderrequest-cancel', kwargs={'pk': request_obj.pk})
        data = {'admin_notes': 'Duplicate request'}
        response = client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'cancelled'

        # Verify in database
        request_obj.refresh_from_db()
        assert request_obj.status == 'cancelled'
        assert request_obj.reviewed_by == user
        assert request_obj.admin_notes == 'Duplicate request'

    def test_generate_cart_links(self, authenticated_client):
        """Test generating shopping cart links."""
        client, user = authenticated_client

        supplier1 = SupplierFactory(supplier_type='amazon')
        supplier2 = SupplierFactory(supplier_type='grainger')

        item1 = InventoryItemFactory(
            supplier=supplier1,
            supplier_sku='AMZN-123',
            supplier_url='https://amazon.com/item1'
        )
        item2 = InventoryItemFactory(
            supplier=supplier2,
            supplier_sku='GRNG-456',
            supplier_url='https://grainger.com/item2'
        )

        ReorderRequestFactory(item=item1, status='approved')
        ReorderRequestFactory(item=item2, status='approved')

        url = reverse('reorderrequest-generate-cart-links')
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert 'amazon' in response.data
        assert 'grainger' in response.data
        assert len(response.data['amazon']['items']) == 1
        assert len(response.data['grainger']['items']) == 1
