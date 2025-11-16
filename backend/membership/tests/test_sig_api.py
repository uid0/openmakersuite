"""
API tests for SIG management endpoints.
"""

from django.contrib.auth.models import Group
from django.urls import reverse

import pytest
from rest_framework import status

from inventory.tests.factories import AssetFactory, InventoryItemFactory
from membership.models import SIGAdmin
from membership.tests.factories import UserFactory


@pytest.mark.integration
class TestSIGAPI:
    """Tests for SIG API endpoints."""

    def test_list_sigs_requires_auth(self, api_client):
        """Test listing SIGs requires authentication."""
        url = reverse("sig-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_sigs_as_sig_admin(self, authenticated_client):
        """Test listing SIGs as a SIG admin."""
        client, user = authenticated_client
        group1 = Group.objects.create(name="SIG 1")
        group2 = Group.objects.create(name="SIG 2")
        SIGAdmin.objects.create(user=user, group=group1, is_active=True)
        SIGAdmin.objects.create(user=user, group=group2, is_active=True)

        url = reverse("sig-list")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

    def test_list_sigs_as_staff(self, authenticated_client):
        """Test staff can see all SIGs."""
        client, user = authenticated_client
        user.is_staff = True
        user.save()

        Group.objects.create(name="SIG 1")
        Group.objects.create(name="SIG 2")

        url = reverse("sig-list")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) >= 2

    def test_get_sig_details(self, authenticated_client):
        """Test getting SIG details."""
        client, user = authenticated_client
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=user, group=group, is_active=True)

        url = reverse("sig-details", kwargs={"pk": group.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Test SIG"
        assert response.data["is_user_admin"] is True

    def test_get_sig_details_unauthorized(self, authenticated_client):
        """Test getting SIG details without permission."""
        client, user = authenticated_client
        other_user = UserFactory()
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=other_user, group=group, is_active=True)

        url = reverse("sig-details", kwargs={"pk": group.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.integration
class TestSIGMemberAPI:
    """Tests for SIG member management endpoints."""

    def test_list_sig_members(self, authenticated_client):
        """Test listing SIG members."""
        client, admin_user = authenticated_client
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=admin_user, group=group, is_active=True)

        member1 = UserFactory()
        member2 = UserFactory()
        group.user_set.add(member1, member2)

        url = reverse("sig-member-list", kwargs={"sig_pk": group.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

    def test_list_sig_members_unauthorized(self, authenticated_client):
        """Test listing members without permission."""
        client, user = authenticated_client
        other_user = UserFactory()
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=other_user, group=group, is_active=True)

        url = reverse("sig-member-list", kwargs={"sig_pk": group.pk})
        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_add_sig_member(self, authenticated_client):
        """Test adding a member to a SIG."""
        client, admin_user = authenticated_client
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=admin_user, group=group, is_active=True)

        new_member = UserFactory()

        url = reverse("sig-member-list", kwargs={"sig_pk": group.pk})
        response = client.post(url, {"user_id": new_member.id})

        assert response.status_code == status.HTTP_201_CREATED
        assert new_member in group.user_set.all()

    def test_remove_sig_member(self, authenticated_client):
        """Test removing a member from a SIG."""
        client, admin_user = authenticated_client
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=admin_user, group=group, is_active=True)

        member = UserFactory()
        group.user_set.add(member)

        url = reverse("sig-member-detail", kwargs={"sig_pk": group.pk, "pk": member.id})
        response = client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert member not in group.user_set.all()


@pytest.mark.integration
class TestAssetSIGPermissions:
    """Tests for asset permissions with SIG ownership."""

    def test_list_assets_filtered_by_sig(self, authenticated_client):
        """Test that SIG admins only see their SIG's assets."""
        client, sig_admin = authenticated_client

        sig_group = Group.objects.create(name="Test SIG")
        other_group = Group.objects.create(name="Other SIG")
        SIGAdmin.objects.create(user=sig_admin, group=sig_group, is_active=True)

        # Create assets
        sig_asset = AssetFactory(owning_group=sig_group)
        other_asset = AssetFactory(owning_group=other_group)
        space_asset = AssetFactory(owning_group=None)

        url = reverse("asset-list")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        asset_ids = [a["id"] for a in response.data["results"]]
        assert str(sig_asset.id) in asset_ids
        assert str(other_asset.id) not in asset_ids
        assert str(space_asset.id) not in asset_ids

    def test_create_asset_for_sig(self, authenticated_client):
        """Test creating an asset for a SIG."""
        client, sig_admin = authenticated_client
        sig_group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=sig_admin, group=sig_group, is_active=True)

        from inventory.tests.factories import CategoryFactory

        category = CategoryFactory()

        url = reverse("asset-list")
        data = {
            "name": "Test Asset",
            "description": "Test description",
            "ownership_type": "group",
            "owning_group": sig_group.id,
            "category": category.id,
            "status": "active",
        }
        response = client.post(url, data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["owning_group"] == sig_group.id

    def test_create_asset_for_other_sig_forbidden(self, authenticated_client):
        """Test creating asset for another SIG is forbidden."""
        client, sig_admin = authenticated_client
        other_user = UserFactory()
        sig_group = Group.objects.create(name="Test SIG")
        other_group = Group.objects.create(name="Other SIG")
        SIGAdmin.objects.create(user=sig_admin, group=sig_group, is_active=True)
        SIGAdmin.objects.create(user=other_user, group=other_group, is_active=True)

        from inventory.tests.factories import CategoryFactory

        category = CategoryFactory()

        url = reverse("asset-list")
        data = {
            "name": "Test Asset",
            "description": "Test description",
            "ownership_type": "group",
            "owning_group": other_group.id,
            "category": category.id,
            "status": "active",
        }
        response = client.post(url, data)

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.integration
class TestInventorySIGPermissions:
    """Tests for inventory permissions with SIG ownership."""

    def test_list_inventory_filtered_by_sig(self, authenticated_client):
        """Test that SIG admins only see their SIG's inventory."""
        client, sig_admin = authenticated_client

        sig_group = Group.objects.create(name="Test SIG")
        other_group = Group.objects.create(name="Other SIG")
        SIGAdmin.objects.create(user=sig_admin, group=sig_group, is_active=True)

        # Create inventory items
        sig_item = InventoryItemFactory(owning_group=sig_group)
        other_item = InventoryItemFactory(owning_group=other_group)
        space_item = InventoryItemFactory(owning_group=None)

        url = reverse("inventoryitem-list")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        item_ids = [i["id"] for i in response.data["results"]]
        assert str(sig_item.id) in item_ids
        assert str(other_item.id) not in item_ids
        assert str(space_item.id) not in item_ids

    def test_my_sig_inventory_endpoint(self, authenticated_client):
        """Test my_sig_inventory endpoint."""
        client, sig_admin = authenticated_client
        sig_group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=sig_admin, group=sig_group, is_active=True)

        InventoryItemFactory(owning_group=sig_group)
        InventoryItemFactory(owning_group=sig_group)

        url = reverse("inventoryitem-my-sig-inventory")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2


@pytest.mark.integration
class TestReorderRequestSIGPermissions:
    """Tests for reorder request permissions with SIG ownership."""

    def test_sig_pending_requests(self, authenticated_client):
        """Test getting pending requests for SIG's inventory."""
        client, sig_admin = authenticated_client
        sig_group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=sig_admin, group=sig_group, is_active=True)

        from reorder_queue.models import ReorderRequest
        from reorder_queue.tests.factories import ReorderRequestFactory

        sig_item = InventoryItemFactory(owning_group=sig_group)
        other_item = InventoryItemFactory(owning_group=None)

        sig_request = ReorderRequestFactory(item=sig_item, status=ReorderRequest.PENDING)
        other_request = ReorderRequestFactory(item=other_item, status=ReorderRequest.PENDING)

        url = reverse("reorderrequest-sig-pending")
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        request_ids = [r["id"] for r in response.data]
        assert sig_request.id in request_ids
        assert other_request.id not in request_ids
