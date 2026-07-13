"""
Views for checklist API.
"""

from django.db import models
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from inventory.models import Asset, InventoryItem, Location
from membership.utils import get_user_managed_sigs, is_logistics_member, is_sig_admin

from .models import Checklist, ChecklistCompletion, ChecklistStep, ChecklistStepCompletion
from .serializers import (
    ChecklistCompletionSerializer,
    ChecklistListSerializer,
    ChecklistSerializer,
    ChecklistStepScanSerializer,
)


class ChecklistViewSet(viewsets.ModelViewSet):
    """
    ViewSet for checklist management.

    SIG admins can create/edit checklists for their SIGs.
    Public endpoints allow viewing and starting checklists.
    """

    queryset = Checklist.objects.select_related("sig", "created_by").prefetch_related("steps")
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_serializer_class(self):
        """Use different serializers for list vs detail."""
        if self.action == "list":
            return ChecklistListSerializer
        return ChecklistSerializer

    def get_queryset(self):
        """Filter checklists based on user permissions."""
        queryset = super().get_queryset()
        user = self.request.user

        # Public actions (start, get_detail) can access public checklists
        if self.action in ["start", "get_detail"]:
            # Allow access to public checklists for these actions
            # get_object() will handle permission checks within the action
            return queryset

        # For list action, filter by active status
        if self.action == "list":
            queryset = queryset.filter(is_active=True)

        # Superusers and staff can see all checklists
        if user.is_superuser or user.is_staff or is_logistics_member(user):
            return queryset

        # SIG admins can see checklists for their SIGs
        if user.is_authenticated:
            user_sigs = get_user_managed_sigs(user)
            if user_sigs.exists():
                return queryset.filter(sig__in=user_sigs)

        # Unauthenticated users or users without SIG admin access see nothing
        return queryset.none()

    def perform_create(self, serializer):
        """Only SIG admins, staff, or logistics can create checklists."""
        user = self.request.user
        sig = serializer.validated_data.get("sig")

        if not (
            user.is_superuser
            or user.is_staff
            or is_logistics_member(user)
            or (sig and is_sig_admin(user, sig))
        ):
            raise PermissionError("You do not have permission to create checklists.")

        serializer.save(created_by=user)

    def perform_update(self, serializer):
        """Only SIG admins, staff, or logistics can update checklists."""
        checklist = self.get_object()
        user = self.request.user

        if not (
            user.is_superuser
            or user.is_staff
            or is_logistics_member(user)
            or is_sig_admin(user, checklist.sig)
        ):
            raise PermissionError("You do not have permission to update this checklist.")

        serializer.save()

    def perform_destroy(self, instance):
        """Only SIG admins, staff, or logistics can delete checklists."""
        user = self.request.user

        if not (
            user.is_superuser
            or user.is_staff
            or is_logistics_member(user)
            or is_sig_admin(user, instance.sig)
        ):
            raise PermissionError("You do not have permission to delete this checklist.")

        instance.delete()

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def available(self, request):
        """
        List checklists available to the current user.

        For unauthenticated users, returns only public checklists.
        For authenticated users, returns public checklists plus checklists
        for SIGs they belong to.
        """
        queryset = Checklist.objects.filter(is_active=True)
        user = request.user

        if not user.is_authenticated:
            # Unauthenticated users only see public checklists
            queryset = queryset.filter(is_public=True)
        else:
            # Authenticated users see public checklists plus checklists for their SIGs
            if not (user.is_superuser or user.is_staff or is_logistics_member(user)):
                user_sigs = get_user_managed_sigs(user)
                if user_sigs.exists():
                    queryset = queryset.filter(
                        models.Q(is_public=True) | models.Q(sig__in=user_sigs)
                    )
                else:
                    queryset = queryset.filter(is_public=True)

        # Filter by asset, location, or item if provided
        asset_id = request.query_params.get("asset_id")
        location_id = request.query_params.get("location_id")
        item_id = request.query_params.get("item_id")

        if asset_id:
            queryset = queryset.filter(steps__asset_id=asset_id).distinct()
        elif location_id:
            queryset = queryset.filter(steps__location_id=location_id).distinct()
        elif item_id:
            queryset = queryset.filter(steps__inventory_item_id=item_id).distinct()

        serializer = ChecklistListSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], permission_classes=[AllowAny], url_path="detail")
    def get_detail(self, request, **kwargs):
        """Get full checklist details (public if is_public=True)."""
        checklist = self.get_object()
        user = request.user

        # Check if user can view this checklist
        if not checklist.is_public:
            if not user.is_authenticated:
                return Response(
                    {"detail": "This checklist is not publicly available."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # Authenticated users can view if they're staff, logistics, or SIG admin
            if not (
                user.is_superuser
                or user.is_staff
                or is_logistics_member(user)
                or is_sig_admin(user, checklist.sig)
            ):
                return Response(
                    {"detail": "You do not have permission to view this checklist."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        serializer = ChecklistSerializer(checklist)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[AllowAny])
    def start(self, request, **kwargs):
        """Start a new checklist completion."""
        checklist = self.get_object()
        user = request.user

        # Check if user can access this checklist
        if not checklist.is_public:
            if not user.is_authenticated:
                return Response(
                    {"detail": "This checklist is not publicly available."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if not (
                user.is_superuser
                or user.is_staff
                or is_logistics_member(user)
                or is_sig_admin(user, checklist.sig)
            ):
                return Response(
                    {"detail": "You do not have permission to start this checklist."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Check if checklist is active
        if not checklist.is_active:
            return Response(
                {"detail": "This checklist is not currently active."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create completion record
        user_name = request.data.get("user_name", "")
        if not isinstance(user_name, str):
            user_name = ""

        # If user is authenticated and no user_name provided, use their display name
        if user.is_authenticated and not user_name:
            # Prefer handle over username for display
            user_name = user.handle or user.username

        completion = ChecklistCompletion.objects.create(
            checklist=checklist,
            user=user if user.is_authenticated else None,
            user_name=user_name,
        )

        response_serializer = ChecklistCompletionSerializer(completion)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class ChecklistCompletionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for checklist completion records.

    Allows viewing and managing checklist completions.
    """

    queryset = ChecklistCompletion.objects.select_related(
        "checklist", "user", "checklist__sig"
    ).prefetch_related("step_completions__step")
    serializer_class = ChecklistCompletionSerializer
    permission_classes = [AllowAny]
    lookup_field = "id"

    def get_queryset(self):
        """Filter completions based on user permissions."""
        queryset = super().get_queryset()
        user = self.request.user

        # Filter by checklist if provided
        checklist_id = self.request.query_params.get("checklist")
        if checklist_id:
            queryset = queryset.filter(checklist_id=checklist_id)

        # Unauthenticated users can only see their own completions (by completion ID)
        if not user.is_authenticated:
            # This will be filtered by detail action
            return queryset

        # Authenticated users can see their own completions
        if not (user.is_superuser or user.is_staff or is_logistics_member(user)):
            return queryset.filter(user=user)

        # Staff/logistics can see all completions
        return queryset

    @action(detail=True, methods=["post"], permission_classes=[AllowAny])
    def scan(self, request, id=None):
        """Record a step scan for a checklist completion."""
        completion = self.get_object()
        user = request.user

        # Verify user has access to this completion
        if not user.is_authenticated:
            # For unauthenticated users, allow if they provided user_name
            if completion.user is not None:
                return Response(
                    {"detail": "This completion belongs to an authenticated user."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            # For authenticated users, must be the owner or staff
            if not (
                completion.user == user
                or user.is_superuser
                or user.is_staff
                or is_logistics_member(user)
            ):
                return Response(
                    {"detail": "You do not have permission to modify this completion."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Validate scan data
        scan_serializer = ChecklistStepScanSerializer(data=request.data)
        scan_serializer.is_valid(raise_exception=True)

        step_id = scan_serializer.validated_data["step_id"]
        asset_id = scan_serializer.validated_data.get("asset_id")
        location_id = scan_serializer.validated_data.get("location_id")
        item_id = scan_serializer.validated_data.get("item_id")
        notes = scan_serializer.validated_data.get("notes", "")
        photo = scan_serializer.validated_data.get("photo")
        photo_caption = scan_serializer.validated_data.get("photo_caption", "")

        # Get the step
        try:
            step = ChecklistStep.objects.get(id=step_id, checklist=completion.checklist)
        except ChecklistStep.DoesNotExist:
            return Response(
                {"detail": "Step not found in this checklist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if step.requires_photo and not photo:
            return Response(
                {"detail": "This step requires a photo upload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify the scanned item matches the step. Branch on the typed-target
        # accessor (#884) rather than hand-written asset/location/item FK checks;
        # the scan-input triplet keeps its asset_id/item_id (UUID) vs location_id
        # (int) asymmetry.
        scanned_asset = None
        scanned_location = None
        scanned_item = None
        if step.target_type == "asset":
            if not asset_id or str(step.asset_id) != str(asset_id):
                return Response(
                    {"detail": "Scanned asset does not match this step."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            scanned_asset = Asset.objects.get(id=asset_id)
        elif step.target_type == "location":
            if not location_id or step.location_id != location_id:
                return Response(
                    {"detail": "Scanned location does not match this step."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            scanned_location = Location.objects.get(id=location_id)
        elif step.target_type == "inventory_item":
            if not item_id or str(step.inventory_item_id) != str(item_id):
                return Response(
                    {"detail": "Scanned item does not match this step."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            scanned_item = InventoryItem.objects.get(id=item_id)
        else:
            return Response(
                {"detail": "Step has no associated asset, location, or item."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create or update step completion
        defaults = {
            "scanned_asset": scanned_asset,
            "scanned_location": scanned_location,
            "scanned_item": scanned_item,
            "notes": notes,
            "photo_caption": photo_caption,
        }
        if photo:
            defaults["photo"] = photo

        step_completion, created = ChecklistStepCompletion.objects.get_or_create(
            completion=completion,
            step=step,
            defaults=defaults,
        )

        if not created:
            # Update existing completion
            step_completion.scanned_at = timezone.now()
            step_completion.scanned_asset = scanned_asset
            step_completion.scanned_location = scanned_location
            step_completion.scanned_item = scanned_item
            if notes:
                step_completion.notes = notes
            if photo:
                step_completion.photo = photo
            if photo_caption:
                step_completion.photo_caption = photo_caption
            step_completion.save()

        # Reload completion to get updated step completions
        completion.refresh_from_db()
        response_serializer = ChecklistCompletionSerializer(
            completion, context={"request": request}
        )
        return Response(response_serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[AllowAny])
    def complete(self, request, id=None):
        """Mark a checklist completion as completed."""
        completion = self.get_object()
        user = request.user

        # Verify user has access to this completion
        if not user.is_authenticated:
            if completion.user is not None:
                return Response(
                    {"detail": "This completion belongs to an authenticated user."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            if not (
                completion.user == user
                or user.is_superuser
                or user.is_staff
                or is_logistics_member(user)
            ):
                return Response(
                    {"detail": "You do not have permission to modify this completion."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Check if all required steps are completed
        required_steps = completion.checklist.steps.filter(required=True)
        completed_step_ids = set(completion.step_completions.values_list("step_id", flat=True))

        missing_steps = [step for step in required_steps if step.id not in completed_step_ids]
        if missing_steps:
            return Response(
                {
                    "detail": "Not all required steps are completed.",
                    "missing_steps": [
                        {
                            "id": str(step.id),
                            "name": step.name,
                            "step_number": step.step_number,
                        }
                        for step in missing_steps
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Mark as completed
        completion.mark_completed()

        response_serializer = ChecklistCompletionSerializer(completion)
        return Response(response_serializer.data)
