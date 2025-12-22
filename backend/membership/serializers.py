"""
Serializers for membership and SIG management API.
"""

from django.contrib.auth.models import Group

from rest_framework import serializers

from .models import SIGAdmin, User, UserRegistrationToken


class SIGAdminSerializer(serializers.ModelSerializer):
    """Serializer for SIGAdmin model."""

    user_id = serializers.IntegerField(source="user.id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    group_id = serializers.IntegerField(source="group.id", read_only=True)
    group_name = serializers.CharField(source="group.name", read_only=True)

    class Meta:
        model = SIGAdmin
        fields = [
            "id",
            "user",
            "user_id",
            "username",
            "user_email",
            "group",
            "group_id",
            "group_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class SIGMemberSerializer(serializers.Serializer):
    """Serializer for SIG members (users in a Group)."""

    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    handle = serializers.CharField(read_only=True)
    is_sig_admin = serializers.SerializerMethodField()

    def get_is_sig_admin(self, obj):
        """Check if this user is a SIG admin for the group."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        group = self.context.get("group")
        if not group:
            return False
        return SIGAdmin.is_sig_admin(obj, group)


class SIGSerializer(serializers.ModelSerializer):
    """Serializer for SIG (Group) with metadata."""

    member_count = serializers.SerializerMethodField()
    asset_count = serializers.SerializerMethodField()
    inventory_count = serializers.SerializerMethodField()
    admins = serializers.SerializerMethodField()
    is_user_admin = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = [
            "id",
            "name",
            "member_count",
            "asset_count",
            "inventory_count",
            "admins",
            "is_user_admin",
        ]

    def get_member_count(self, obj):
        """Get the number of members in this SIG."""
        try:
            return obj.user_set.count()
        except Exception:
            # If auth_user_groups table doesn't exist, return 0
            return 0

    def get_asset_count(self, obj):
        """Get the number of assets owned by this SIG."""
        from inventory.models import Asset

        return Asset.objects.filter(owning_group=obj).count()

    def get_inventory_count(self, obj):
        """Get the number of inventory items owned by this SIG."""
        from inventory.models import InventoryItem

        return InventoryItem.objects.filter(owning_group=obj).count()

    def get_admins(self, obj):
        """Get list of admin users for this SIG."""
        admins = SIGAdmin.get_sig_admins(obj)
        return [
            {
                "id": admin.id,
                "username": admin.username,
                "email": admin.email,
                "handle": admin.handle,
            }
            for admin in admins
        ]

    def get_is_user_admin(self, obj):
        """Check if the current user is an admin of this SIG."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return SIGAdmin.is_sig_admin(request.user, obj)


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model (for member management)."""

    class Meta:
        model = User
        fields = ["id", "username", "email", "handle", "first_name", "last_name"]
        read_only_fields = [
            "id",
            "username",
            "email",
            "handle",
            "first_name",
            "last_name",
        ]


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for user profile editing."""

    signature_image_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "handle",
            "discord_username",
            "discourse_username",
            "signature_image_url",
        ]
        read_only_fields = ["id", "username", "signature_image_url"]

    def get_signature_image_url(self, obj):
        """Return the URL of the signature image if it exists."""
        if obj.signature_image:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.signature_image.url)
            return obj.signature_image.url
        return None


class ChangePasswordSerializer(serializers.Serializer):
    """Serializer for password change."""

    old_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True, min_length=8)
    new_password2 = serializers.CharField(required=True, write_only=True)

    def validate(self, attrs):
        """Validate that new passwords match."""
        if attrs["new_password"] != attrs["new_password2"]:
            raise serializers.ValidationError({"new_password2": "New passwords do not match."})
        return attrs


class UserRegistrationTokenSerializer(serializers.ModelSerializer):
    """Serializer for user registration token."""

    user = UserSerializer(read_only=True)
    registration_url = serializers.SerializerMethodField()
    is_valid = serializers.SerializerMethodField()

    class Meta:
        model = UserRegistrationToken
        fields = [
            "id",
            "user",
            "token",
            "registration_url",
            "is_valid",
            "used",
            "used_at",
            "expires_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "token",
            "registration_url",
            "is_valid",
            "used",
            "used_at",
            "expires_at",
            "created_at",
        ]

    def get_registration_url(self, obj):
        """Get the registration URL."""
        return obj.get_registration_url()

    def get_is_valid(self, obj):
        """Check if token is valid."""
        return obj.is_valid()


class TokenValidationSerializer(serializers.Serializer):
    """Serializer for token validation."""

    token = serializers.CharField(required=True)

    def validate_token(self, value):
        """Validate that token exists and is valid."""
        try:
            registration_token = UserRegistrationToken.objects.get(token=value)
        except UserRegistrationToken.DoesNotExist:
            raise serializers.ValidationError("Invalid registration token.")

        if not registration_token.is_valid():
            if registration_token.used:
                raise serializers.ValidationError("This registration token has already been used.")
            else:
                raise serializers.ValidationError("This registration token has expired.")

        return value


class UserRegistrationSerializer(serializers.Serializer):
    """Serializer for user registration with token."""

    token = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True, min_length=8)
    password2 = serializers.CharField(required=True, write_only=True)

    def validate(self, attrs):
        """Validate registration data."""
        # Validate token
        token = attrs.get("token")
        try:
            registration_token = UserRegistrationToken.objects.get(token=token)
        except UserRegistrationToken.DoesNotExist:
            raise serializers.ValidationError({"token": "Invalid registration token."})

        if not registration_token.is_valid():
            if registration_token.used:
                raise serializers.ValidationError(
                    {"token": "This registration token has already been used."}
                )
            else:
                raise serializers.ValidationError({"token": "This registration token has expired."})

        # Validate passwords match
        password = attrs.get("password")
        password2 = attrs.get("password2")
        if password != password2:
            raise serializers.ValidationError({"password2": "Passwords do not match."})

        return attrs

    def save(self):
        """Complete user registration by setting password."""
        token = self.validated_data["token"]
        password = self.validated_data["password"]

        registration_token = UserRegistrationToken.objects.get(token=token)
        user = registration_token.user

        # Set password
        user.set_password(password)
        user.save()

        # Mark token as used
        registration_token.mark_as_used()

        return user
