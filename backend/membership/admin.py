"""
Admin configuration for membership app.
"""

from io import BytesIO

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html

from inventory.services.qr_code_service import QRCodeService

from .models import (
    Committee,
    CommitteeChair,
    Membership,
    SIGAdmin,
    SIGCommittee,
    UserRegistrationToken,
)

User = get_user_model()


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Admin interface for custom user model."""

    list_display = [
        "username",
        "handle",
        "email",
        "first_name",
        "last_name",
        "active_directory_username",
        "badge_number",
        "discord_username",
        "discourse_username",
        "is_board_member",
        "is_officer",
        "is_director",
        "is_staff",
        "is_active",
        "date_joined",
        "registration_token_status",
    ]
    list_filter = [
        "is_staff",
        "is_superuser",
        "is_active",
        "is_board_member",
        "is_officer",
        "is_director",
        "groups",
        "date_joined",
    ]
    search_fields = [
        "username",
        "handle",
        "email",
        "first_name",
        "last_name",
        "active_directory_username",
        "badge_number",
        "discord_username",
        "discourse_username",
    ]
    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "username",
                    "handle",
                    "password",
                    "email",
                    "first_name",
                    "last_name",
                )
            },
        ),
        (
            "Makerspace Information",
            {
                "fields": (
                    "active_directory_username",
                    "badge_number",
                    "discord_username",
                    "discourse_username",
                    "is_board_member",
                    "is_officer",
                    "is_director",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Important dates",
            {
                "fields": (
                    "last_login",
                    "date_joined",
                )
            },
        ),
    )
    readonly_fields = ["last_login", "date_joined"]
    actions = ["create_registration_token", "generate_registration_qr"]

    def save_model(self, request, obj, form, change):
        """Override save to automatically create registration token for new users."""
        # Save the user first
        super().save_model(request, obj, form, change)

        # If this is a new user (not a change), create a registration token
        if not change:
            # Check if user already has a token (shouldn't happen for new users, but be safe)
            if not hasattr(obj, "registration_token"):
                UserRegistrationToken.create_for_user(
                    user=obj,
                    created_by=request.user,
                )

    @admin.display(description="Registration Token")
    def registration_token_status(self, obj):
        """Display registration token status with link to QR code."""
        try:
            token = obj.registration_token
            if token.used:
                return format_html('<span style="color: gray;">Used</span>')
            elif not token.is_valid():
                return format_html('<span style="color: red;">Expired</span>')
            else:
                qr_url = reverse("admin:membership_user_registrationtoken_qr", args=[token.id])
                return format_html(
                    '<a href="{}" target="_blank">View QR Code</a>',
                    qr_url,
                )
        except UserRegistrationToken.DoesNotExist:
            return format_html('<span style="color: gray;">No token</span>')

    @admin.action(description="Create registration token and QR code for selected users")
    def create_registration_token(self, request, queryset):
        """Create registration tokens for selected users."""
        created_count = 0
        for user in queryset:
            # Check if user already has an active token
            if hasattr(user, "registration_token") and user.registration_token.is_valid():
                continue

            # Create new token
            UserRegistrationToken.create_for_user(
                user=user,
                created_by=request.user,
            )
            created_count += 1

        if created_count > 0:
            self.message_user(
                request,
                f"Successfully created {created_count} registration token(s).",
            )
        else:
            self.message_user(
                request,
                "No tokens created. Users may already have active tokens.",
                level="warning",
            )

    @admin.action(description="Generate QR codes for users with active tokens")
    def generate_registration_qr(self, request, queryset):
        """Generate QR codes for users with active registration tokens."""
        generated_count = 0
        for user in queryset:
            try:
                token = user.registration_token
                if token.is_valid():
                    # QR code will be generated on-demand via the view
                    generated_count += 1
            except UserRegistrationToken.DoesNotExist:
                continue

        if generated_count > 0:
            self.message_user(
                request,
                f"QR codes available for {generated_count} user(s). "
                "Click 'View QR Code' in the list to download.",
            )
        else:
            self.message_user(
                request,
                "No active tokens found. Create tokens first.",
                level="warning",
            )

    def get_urls(self):
        """Add custom URLs for registration token QR code generation."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:user_id>/create-token/",
                self.admin_site.admin_view(self.create_token_view),
                name="membership_user_create_token",
            ),
        ]
        return custom_urls + urls

    def create_token_view(self, request, user_id):
        """Create a registration token for a specific user."""
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return HttpResponse("User not found", status=404)

        # Check if user already has an active token
        if hasattr(user, "registration_token") and user.registration_token.is_valid():
            return HttpResponse(
                "User already has an active registration token.",
                status=400,
            )

        # Create new token
        token = UserRegistrationToken.create_for_user(
            user=user,
            created_by=request.user,
        )

        # Redirect to token admin page
        return redirect(reverse("admin:membership_userregistrationtoken_change", args=[token.id]))


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    """Admin interface for Membership model."""

    list_display = [
        "id",
        "membership_type",
        "status",
        "get_users",
        "start_date",
        "end_date",
        "created_at",
    ]
    list_filter = [
        "membership_type",
        "status",
        "start_date",
        "end_date",
        "created_at",
    ]
    search_fields = [
        "users__username",
        "users__email",
        "users__handle",
        "notes",
    ]
    filter_horizontal = ["users"]
    fieldsets = (
        (
            "Membership Information",
            {
                "fields": (
                    "membership_type",
                    "status",
                    "users",
                )
            },
        ),
        (
            "Dates",
            {
                "fields": (
                    "start_date",
                    "end_date",
                )
            },
        ),
        (
            "Notes",
            {
                "fields": ("notes",),
            },
        ),
    )
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Users")
    def get_users(self, obj):
        """Display users associated with this membership."""
        users = obj.users.all()
        if users:
            return ", ".join([user.username for user in users[:5]])
        return "No users"


class SIGAdminInline(admin.TabularInline):
    """Inline admin for managing SIG admins on Groups."""

    model = SIGAdmin
    extra = 1
    fields = ["user", "is_active"]
    autocomplete_fields = ["user"]


@admin.register(SIGAdmin)
class SIGAdminAdmin(admin.ModelAdmin):
    """Admin interface for SIGAdmin model."""

    list_display = ["user", "group", "is_active", "created_at"]
    list_filter = ["is_active", "group", "created_at"]
    search_fields = ["user__username", "user__email", "group__name"]
    autocomplete_fields = ["user", "group"]
    fieldsets = (
        (
            "SIG Admin Information",
            {
                "fields": (
                    "user",
                    "group",
                    "is_active",
                )
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
    readonly_fields = ["created_at", "updated_at"]


# Register inline for Group admin
admin.site.unregister(Group)  # Unregister default Group admin


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    """Admin interface for Group (SIG) model with SIG admin management."""

    list_display = ["name", "get_sig_admin_count", "get_member_count"]
    search_fields = ["name"]
    filter_horizontal = ["permissions"]
    inlines = [SIGAdminInline]

    @admin.display(description="SIG Admins")
    def get_sig_admin_count(self, obj):
        """Display count of SIG admins for this group."""
        count = SIGAdmin.objects.filter(group=obj, is_active=True).count()
        return count

    @admin.display(description="Members")
    def get_member_count(self, obj):
        """Display count of members in this group."""
        return obj.user_set.count()


@admin.register(UserRegistrationToken)
class UserRegistrationTokenAdmin(admin.ModelAdmin):
    """Admin interface for User Registration Token."""

    list_display = [
        "user",
        "token_preview",
        "is_valid_display",
        "used",
        "expires_at",
        "created_at",
        "created_by",
        "qr_code_link",
    ]
    list_filter = ["used", "created_at", "expires_at"]
    search_fields = ["user__username", "user__email", "token"]
    readonly_fields = [
        "token",
        "user",
        "used",
        "used_at",
        "expires_at",
        "created_at",
        "created_by",
        "registration_url",
        "qr_code_preview",
    ]
    fieldsets = (
        (
            "Token Information",
            {
                "fields": (
                    "user",
                    "token",
                    "registration_url",
                    "used",
                    "used_at",
                    "expires_at",
                )
            },
        ),
        (
            "QR Code",
            {
                "fields": ("qr_code_preview",),
                "description": "Scan this QR code to complete user registration.",
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "created_at",
                    "created_by",
                )
            },
        ),
    )

    def get_urls(self):
        """Add custom URLs for QR code generation."""
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:token_id>/qr/",
                self.admin_site.admin_view(self.qr_code_view),
                name="membership_user_registrationtoken_qr",
            ),
        ]
        return custom_urls + urls

    def qr_code_view(self, request, token_id):
        """Generate and return QR code image for registration token."""
        try:
            token = UserRegistrationToken.objects.get(pk=token_id)
        except UserRegistrationToken.DoesNotExist:
            return HttpResponse("Token not found", status=404)

        # Generate QR code
        registration_url = token.get_registration_url()
        service = QRCodeService(include_logo=True)
        qr_img = service.generate_qr_code_image(registration_url)

        # Convert to BytesIO
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        # Return as HTTP response
        response = HttpResponse(buffer.read(), content_type="image/png")
        response["Content-Disposition"] = f'inline; filename="registration_qr_{token.id}.png"'
        return response

    @admin.display(description="Token")
    def token_preview(self, obj):
        """Display a preview of the token."""
        return f"{obj.token[:16]}..." if len(obj.token) > 16 else obj.token

    @admin.display(description="Status")
    def is_valid_display(self, obj):
        """Display token validity status."""
        if obj.used:
            return format_html('<span style="color: gray;">Used</span>')
        elif not obj.is_valid():
            return format_html('<span style="color: red;">Expired</span>')
        else:
            return format_html('<span style="color: green;">Active</span>')

    @admin.display(description="QR Code")
    def qr_code_link(self, obj):
        """Display link to QR code."""
        if obj.is_valid():
            qr_url = reverse("admin:membership_user_registrationtoken_qr", args=[obj.id])
            return format_html(
                '<a href="{}" target="_blank">Download QR Code</a>',
                qr_url,
            )
        return format_html('<span style="color: gray;">N/A</span>')

    @admin.display(description="Registration URL")
    def registration_url(self, obj):
        """Display the registration URL."""
        url = obj.get_registration_url()
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description="QR Code")
    def qr_code_preview(self, obj):
        """Display QR code preview in admin."""
        if obj.is_valid():
            qr_url = reverse("admin:membership_user_registrationtoken_qr", args=[obj.id])
            return format_html(
                '<img src="{}" alt="QR Code" style="max-width: 300px;" /><br/>'
                '<a href="{}" download>Download QR Code</a>',
                qr_url,
                qr_url,
            )
        return format_html('<span style="color: gray;">Token is not active</span>')


@admin.register(Committee)
class CommitteeAdmin(admin.ModelAdmin):
    """Admin interface for Committee model."""

    list_display = ["name", "is_active", "sig_count", "created_at"]
    list_filter = ["is_active", "created_at"]
    search_fields = ["name", "description"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(CommitteeChair)
class CommitteeChairAdmin(admin.ModelAdmin):
    """Admin interface for CommitteeChair model."""

    list_display = ["user", "committee", "is_active", "created_at"]
    list_filter = ["is_active", "committee", "created_at"]
    search_fields = ["user__username", "user__email", "committee__name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(SIGCommittee)
class SIGCommitteeAdmin(admin.ModelAdmin):
    """Admin interface for SIGCommittee model."""

    list_display = ["group", "committee", "created_at"]
    list_filter = ["committee", "created_at"]
    search_fields = ["group__name", "committee__name"]
    readonly_fields = ["created_at", "updated_at"]
