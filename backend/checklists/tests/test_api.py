"""
API tests for checklist endpoints.
"""

from django.contrib.auth import get_user_model

import pytest
from rest_framework import status

from checklists.models import ChecklistCompletion, ChecklistStep
from checklists.tests.factories import (
    ChecklistCompletionFactory,
    ChecklistFactory,
    ChecklistStepFactory,
)
from inventory.tests.factories import AssetFactory, InventoryItemFactory, LocationFactory

User = get_user_model()


@pytest.mark.integration
class TestChecklistAPI:
    """Tests for Checklist API endpoints."""

    def test_list_checklists_requires_auth(self, api_client):
        """Test listing checklists requires authentication for admin actions."""
        ChecklistFactory.create_batch(3)
        url = "/api/checklists/checklists/"
        response = api_client.get(url)

        # List should require auth
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_list_checklists_authenticated(self, authenticated_client):
        """Test listing checklists when authenticated."""
        client, user = authenticated_client
        ChecklistFactory.create_batch(3)
        url = "/api/checklists/checklists/"
        response = client.get(url)

        # Authenticated users can list (but may see empty if not SIG admin)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN]

    def test_available_checklists_public(self, api_client):
        """Test getting available checklists (public endpoint)."""
        public_checklist = ChecklistFactory(is_public=True, is_active=True)
        private_checklist = ChecklistFactory(is_public=False, is_active=True)
        inactive_checklist = ChecklistFactory(is_public=True, is_active=False)

        url = "/api/checklists/checklists/available/"
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        checklist_ids = [c["id"] for c in response.data]
        assert str(public_checklist.id) in checklist_ids
        assert str(private_checklist.id) not in checklist_ids
        assert str(inactive_checklist.id) not in checklist_ids

    def test_available_checklists_filtered_by_asset(self, api_client):
        """Test filtering available checklists by asset."""
        asset = AssetFactory()
        checklist_with_asset = ChecklistFactory(is_public=True, is_active=True)
        ChecklistStep.objects.create(
            checklist=checklist_with_asset,
            step_number=1,
            name="Test Step",
            asset=asset,
            location=None,
            inventory_item=None,
        )

        checklist_without_asset = ChecklistFactory(is_public=True, is_active=True)
        other_asset = AssetFactory()
        ChecklistStep.objects.create(
            checklist=checklist_without_asset,
            step_number=1,
            name="Test Step",
            asset=other_asset,
            location=None,
            inventory_item=None,
        )

        url = "/api/checklists/checklists/available/"
        response = api_client.get(url, {"asset_id": str(asset.id)})

        assert response.status_code == status.HTTP_200_OK
        checklist_ids = [c["id"] for c in response.data]
        assert str(checklist_with_asset.id) in checklist_ids
        assert str(checklist_without_asset.id) not in checklist_ids

    def test_get_checklist_detail_public(self, api_client):
        """Test getting checklist detail (public if is_public=True)."""
        checklist = ChecklistFactory(is_public=True, is_active=True)
        ChecklistStepFactory.create_batch(3, checklist=checklist)

        url = f"/api/checklists/checklists/{checklist.id}/detail/"
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(checklist.id)
        assert response.data["name"] == checklist.name
        assert len(response.data["steps"]) == 3

    def test_get_checklist_detail_private_requires_auth(self, api_client):
        """Test getting private checklist detail requires authentication."""
        checklist = ChecklistFactory(is_public=False, is_active=True)

        url = f"/api/checklists/checklists/{checklist.id}/detail/"
        response = api_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_start_checklist_public(self, api_client):
        """Test starting a checklist (public endpoint)."""
        checklist = ChecklistFactory(is_public=True, is_active=True)

        url = f"/api/checklists/checklists/{checklist.id}/start/"
        response = api_client.post(url, {"user_name": "John Doe"})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["checklist"] == str(checklist.id)
        assert response.data["user_name"] == "John Doe"
        assert response.data["status"] == "in_progress"

        # Verify completion was created
        completion = ChecklistCompletion.objects.get(id=response.data["id"])
        assert completion.checklist == checklist
        assert completion.user is None
        assert completion.user_name == "John Doe"

    def test_start_checklist_authenticated(self, authenticated_client):
        """Test starting a checklist when authenticated."""
        client, user = authenticated_client
        checklist = ChecklistFactory(is_public=True, is_active=True)

        url = f"/api/checklists/checklists/{checklist.id}/start/"
        response = client.post(url, {})

        assert response.status_code == status.HTTP_201_CREATED
        completion = ChecklistCompletion.objects.get(id=response.data["id"])
        assert completion.user == user
        # When authenticated and no user_name provided, should use username
        assert completion.user_name == user.username

    def test_start_checklist_authenticated_with_handle(self, authenticated_client):
        """Test starting a checklist when authenticated with a handle uses handle."""
        client, user = authenticated_client
        # Set a handle for the user
        user.handle = "MyDisplayName"
        user.save()
        checklist = ChecklistFactory(is_public=True, is_active=True)

        url = f"/api/checklists/checklists/{checklist.id}/start/"
        response = client.post(url, {})

        assert response.status_code == status.HTTP_201_CREATED
        completion = ChecklistCompletion.objects.get(id=response.data["id"])
        assert completion.user == user
        # Should prefer handle over username
        assert completion.user_name == "MyDisplayName"

    def test_start_checklist_authenticated_with_explicit_user_name(self, authenticated_client):
        """Test that explicit user_name is respected even when authenticated."""
        client, user = authenticated_client
        user.handle = "MyDisplayName"
        user.save()
        checklist = ChecklistFactory(is_public=True, is_active=True)

        url = f"/api/checklists/checklists/{checklist.id}/start/"
        response = client.post(url, {"user_name": "Custom Name"})

        assert response.status_code == status.HTTP_201_CREATED
        completion = ChecklistCompletion.objects.get(id=response.data["id"])
        assert completion.user == user
        # Explicit user_name should be used
        assert completion.user_name == "Custom Name"

    def test_start_checklist_unauthenticated_without_name(self, api_client):
        """Test that unauthenticated users can start checklists without providing a name."""
        checklist = ChecklistFactory(is_public=True, is_active=True)

        url = f"/api/checklists/checklists/{checklist.id}/start/"
        response = api_client.post(url, {})

        assert response.status_code == status.HTTP_201_CREATED
        completion = ChecklistCompletion.objects.get(id=response.data["id"])
        assert completion.user is None
        assert completion.user_name == ""  # Empty string when not provided

    def test_start_checklist_inactive_fails(self, api_client):
        """Test starting an inactive checklist fails."""
        checklist = ChecklistFactory(is_public=True, is_active=False)

        url = f"/api/checklists/checklists/{checklist.id}/start/"
        response = api_client.post(url, {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
class TestChecklistCompletionAPI:
    """Tests for ChecklistCompletion API endpoints."""

    def test_get_completion_public(self, api_client):
        """Test getting a completion (public endpoint)."""
        completion = ChecklistCompletionFactory(user=None, user_name="John Doe")

        url = f"/api/checklists/completions/{completion.id}/"
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(completion.id)
        assert response.data["user_name"] == "John Doe"

    def test_scan_step_asset(self, api_client):
        """Test scanning a step with an asset."""
        asset = AssetFactory()
        checklist = ChecklistFactory(is_public=True, is_active=True)
        step = ChecklistStepFactory(checklist=checklist, asset=asset)
        completion = ChecklistCompletionFactory(checklist=checklist, user=None, user_name="John")

        url = f"/api/checklists/completions/{completion.id}/scan/"
        response = api_client.post(
            url,
            {
                "step_id": str(step.id),
                "asset_id": str(asset.id),
                "notes": "Scanned successfully",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["step_completions"]) == 1
        step_completion = response.data["step_completions"][0]
        assert step_completion["step"] == str(step.id)
        assert step_completion["scanned_asset"] == str(asset.id)

    def test_scan_step_location(self, api_client):
        """Test scanning a step with a location."""
        location = LocationFactory()
        checklist = ChecklistFactory(is_public=True, is_active=True)
        # Create step with location explicitly
        step = ChecklistStep.objects.create(
            checklist=checklist,
            step_number=1,
            name="Test Step",
            asset=None,
            location=location,
            inventory_item=None,
        )
        completion = ChecklistCompletionFactory(checklist=checklist, user=None, user_name="John")

        url = f"/api/checklists/completions/{completion.id}/scan/"
        response = api_client.post(
            url,
            {
                "step_id": str(step.id),
                "location_id": location.id,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        step_completion = response.data["step_completions"][0]
        assert step_completion["scanned_location"] == location.id

    def test_scan_step_wrong_asset_fails(self, api_client):
        """Test scanning wrong asset fails validation."""
        correct_asset = AssetFactory()
        wrong_asset = AssetFactory()
        checklist = ChecklistFactory(is_public=True, is_active=True)
        # Create step with correct asset
        step = ChecklistStep.objects.create(
            checklist=checklist,
            step_number=1,
            name="Test Step",
            asset=correct_asset,
            location=None,
            inventory_item=None,
        )
        completion = ChecklistCompletionFactory(checklist=checklist, user=None, user_name="John")

        url = f"/api/checklists/completions/{completion.id}/scan/"
        response = api_client.post(
            url,
            {
                "step_id": str(step.id),
                "asset_id": str(wrong_asset.id),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_complete_checklist_all_required_steps(self, api_client):
        """Test completing a checklist when all required steps are done."""
        checklist = ChecklistFactory(is_public=True, is_active=True)
        asset1 = AssetFactory()
        asset2 = AssetFactory()
        step1 = ChecklistStep.objects.create(
            checklist=checklist,
            step_number=1,
            name="Step 1",
            asset=asset1,
            required=True,
        )
        step2 = ChecklistStep.objects.create(
            checklist=checklist,
            step_number=2,
            name="Step 2",
            asset=asset2,
            required=True,
        )
        # Create optional step (not required for completion)
        ChecklistStep.objects.create(
            checklist=checklist,
            step_number=3,
            name="Step 3",
            asset=AssetFactory(),
            required=False,
        )

        completion = ChecklistCompletionFactory(checklist=checklist, user=None, user_name="John")

        # Complete required steps
        url = f"/api/checklists/completions/{completion.id}/scan/"
        api_client.post(url, {"step_id": str(step1.id), "asset_id": str(step1.asset.id)})
        api_client.post(url, {"step_id": str(step2.id), "asset_id": str(step2.asset.id)})

        # Complete checklist
        complete_url = f"/api/checklists/completions/{completion.id}/complete/"
        response = api_client.post(complete_url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "completed"
        assert response.data["completed_at"] is not None

    def test_complete_checklist_missing_required_steps_fails(self, api_client):
        """Test completing a checklist with missing required steps fails."""
        checklist = ChecklistFactory(is_public=True, is_active=True)
        asset1 = AssetFactory()
        asset2 = AssetFactory()
        step1 = ChecklistStep.objects.create(
            checklist=checklist,
            step_number=1,
            name="Step 1",
            asset=asset1,
            required=True,
        )
        # Create second required step (not completed)
        ChecklistStep.objects.create(
            checklist=checklist,
            step_number=2,
            name="Step 2",
            asset=asset2,
            required=True,
        )

        completion = ChecklistCompletionFactory(checklist=checklist, user=None, user_name="John")

        # Complete only one required step
        url = f"/api/checklists/completions/{completion.id}/scan/"
        api_client.post(url, {"step_id": str(step1.id), "asset_id": str(step1.asset.id)})

        # Try to complete checklist
        complete_url = f"/api/checklists/completions/{completion.id}/complete/"
        response = api_client.post(complete_url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "missing_steps" in response.data


@pytest.mark.integration
class TestChecklistIntegrationWithAssets:
    """Tests for checklist integration with asset/location/item endpoints."""

    def test_asset_checklists_endpoint(self, api_client):
        """Test getting checklists for an asset."""
        asset = AssetFactory()
        checklist = ChecklistFactory(is_public=True, is_active=True)
        ChecklistStepFactory(checklist=checklist, asset=asset)

        other_checklist = ChecklistFactory(is_public=True, is_active=True)
        ChecklistStepFactory(checklist=other_checklist, asset=AssetFactory())

        url = f"/api/inventory/assets/{asset.id}/checklists/"
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        checklist_ids = [c["id"] for c in response.data]
        assert str(checklist.id) in checklist_ids
        assert str(other_checklist.id) not in checklist_ids

    def test_location_checklists_endpoint(self, api_client):
        """Test getting checklists for a location."""
        location = LocationFactory()
        checklist = ChecklistFactory(is_public=True, is_active=True)
        ChecklistStep.objects.create(
            checklist=checklist,
            step_number=1,
            name="Test Step",
            asset=None,
            location=location,
            inventory_item=None,
        )

        url = f"/api/inventory/locations/{location.id}/checklists/"
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(checklist.id)

    def test_item_checklists_endpoint(self, api_client):
        """Test getting checklists for an inventory item."""
        item = InventoryItemFactory()
        checklist = ChecklistFactory(is_public=True, is_active=True)
        ChecklistStep.objects.create(
            checklist=checklist,
            step_number=1,
            name="Test Step",
            asset=None,
            location=None,
            inventory_item=item,
        )

        url = f"/api/inventory/items/{item.id}/checklists/"
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["id"] == str(checklist.id)
