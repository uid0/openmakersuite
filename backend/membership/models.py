"""
Models for membership management.
"""

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group
from django.db import models
from django.utils import timezone


class Membership(models.Model):
    """Membership model for makerspace members."""

    MEMBERSHIP_TYPE_MONTHLY = "monthly"
    MEMBERSHIP_TYPE_ANNUAL = "annual"
    MEMBERSHIP_TYPE_COMPLIMENTARY = "complimentary"
    MEMBERSHIP_TYPE_HONORARY = "honorary"

    MEMBERSHIP_TYPE_CHOICES = [
        (MEMBERSHIP_TYPE_MONTHLY, "Monthly"),
        (MEMBERSHIP_TYPE_ANNUAL, "Annual"),
        (MEMBERSHIP_TYPE_COMPLIMENTARY, "Complimentary"),
        (MEMBERSHIP_TYPE_HONORARY, "Honorary"),
    ]

    STATUS_INACTIVE = "inactive"
    STATUS_ACTIVE = "active"
    STATUS_PROBATIVE = "probative"
    STATUS_RESTRICTED = "restricted"
    STATUS_SUSPENDED = "suspended"
    STATUS_TERMINATED = "terminated"

    STATUS_CHOICES = [
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_PROBATIVE, "Probative"),
        (STATUS_RESTRICTED, "Restricted"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_TERMINATED, "Terminated"),
    ]

    membership_type = models.CharField(
        max_length=20,
        choices=MEMBERSHIP_TYPE_CHOICES,
        default=MEMBERSHIP_TYPE_MONTHLY,
        help_text="Type of membership",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_INACTIVE,
        help_text="Current status of the membership",
    )
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="memberships",
        help_text="Users associated with this membership (for families/business accounts)",
        db_table="inventory_membership_users",  # Use existing intermediate table
    )
    start_date = models.DateField(
        null=True,
        blank=True,
        help_text="When the membership started",
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text="When the membership ends (null for active memberships)",
    )
    notes = models.TextField(
        blank=True,
        help_text="Additional notes about this membership",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_membership"  # Use existing table from inventory app
        verbose_name_plural = "Memberships"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        user_names = ", ".join([user.username for user in self.users.all()[:3]])
        if self.users.count() > 3:
            user_names += "..."
        return f"{self.get_membership_type_display()} - {user_names} ({self.get_status_display()})"

    @property
    def is_active(self) -> bool:
        """Check if membership is currently active."""
        return self.status == self.STATUS_ACTIVE


class User(AbstractUser):
    """Custom user model with additional fields for makerspace management."""

    handle = models.CharField(
        max_length=150,
        blank=True,
        unique=True,
        null=True,
        help_text="User's preferred handle/display name",
    )
    active_directory_username = models.CharField(
        max_length=150,
        blank=True,
        help_text="Active Directory username for integration",
    )
    badge_number = models.CharField(
        max_length=50,
        blank=True,
        unique=True,
        null=True,
        help_text="Badge number for physical access control",
    )
    discord_username = models.CharField(
        max_length=150,
        blank=True,
        help_text="Discord username",
    )
    discourse_username = models.CharField(
        max_length=150,
        blank=True,
        help_text="Discourse username",
    )
    is_board_member = models.BooleanField(
        default=False,
        help_text="Indicates if user is a board member",
    )
    is_officer = models.BooleanField(
        default=False,
        help_text="Indicates if user is an officer",
    )
    is_director = models.BooleanField(
        default=False,
        help_text="Indicates if user is a director",
    )
    signature_image = models.ImageField(
        upload_to="signatures/",
        blank=True,
        null=True,
        help_text="PNG signature image for use on tax receipts",
    )
    signature_uploaded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the signature was uploaded",
    )
    signature_line_offset = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        help_text="Y-axis offset for signature line position in PDFs",
    )

    class Meta:
        db_table = "auth_user"  # Keep using the same table name for compatibility

    def can_login(self) -> bool:
        """
        Check if user can log in based on membership status or role.
        Users can log in if:
        - They have an active membership, OR
        - They are staff member, board member, director, or admin
        """
        # Staff, board members, directors, and admins can always log in
        if self.is_staff or self.is_superuser or self.is_board_member or self.is_director:
            return True

        # Check if user has an active membership
        active_memberships = self.memberships.filter(status=Membership.STATUS_ACTIVE)
        return active_memberships.exists()


class SIGAdmin(models.Model):
    """
    Tracks which users are administrators of which SIGs (Special Interest Groups).

    SIGs are represented as Django Groups. This model links users to groups
    and grants them administrative privileges for managing that SIG's resources.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sig_admin_roles",
        help_text="User who is an admin of this SIG",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="sig_admins",
        help_text="SIG (Group) this user administers",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Is this admin role active?",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["user", "group"]]
        ordering = ["group", "user"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["group", "is_active"]),
        ]
        verbose_name = "SIG Admin"
        verbose_name_plural = "SIG Admins"

    def __str__(self) -> str:
        return f"{self.user.username} - {self.group.name}"

    @classmethod
    def is_sig_admin(cls, user, group):
        """Check if a user is an admin of a specific SIG."""
        if not user or not user.is_authenticated or not group:
            return False
        return cls.objects.filter(user=user, group=group, is_active=True).exists()

    @classmethod
    def get_user_sigs(cls, user):
        """Get all SIGs (Groups) that a user administers."""
        if not user or not user.is_authenticated:
            return Group.objects.none()
        return Group.objects.filter(sig_admins__user=user, sig_admins__is_active=True).distinct()

    @classmethod
    def get_sig_admins(cls, group):
        """Get all admin users for a specific SIG."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        if not group:
            return User.objects.none()
        return User.objects.filter(
            sig_admin_roles__group=group, sig_admin_roles__is_active=True
        ).distinct()


class Committee(models.Model):
    """
    Committee model for makerspace committees.

    Committees are organizational structures that contain Special Interest Groups (SIGs).
    Each committee can have one or more chairs who can create and manage SIGs within that committee.
    """

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Name of the committee",
    )
    description = models.TextField(
        blank=True,
        help_text="Description of the committee's purpose and responsibilities",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this committee is currently active",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Committee"
        verbose_name_plural = "Committees"
        indexes = [
            models.Index(fields=["is_active", "name"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def sig_count(self):
        """Get the number of SIGs in this committee."""
        return self.sigs.count()


class CommitteeChair(models.Model):
    """
    Tracks which users are chairs of which committees.

    Committee Chairs can create and manage Special Interest Groups (SIGs)
    within their committee.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="committee_chair_roles",
        help_text="User who is a chair of this committee",
    )
    committee = models.ForeignKey(
        Committee,
        on_delete=models.CASCADE,
        related_name="chairs",
        help_text="Committee this user chairs",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Is this chair role active?",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["user", "committee"]]
        ordering = ["committee", "user"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["committee", "is_active"]),
        ]
        verbose_name = "Committee Chair"
        verbose_name_plural = "Committee Chairs"

    def __str__(self) -> str:
        return f"{self.user.username} - {self.committee.name}"

    @classmethod
    def is_committee_chair(cls, user, committee):
        """Check if a user is a chair of a specific committee."""
        if not user or not user.is_authenticated or not committee:
            return False
        return cls.objects.filter(user=user, committee=committee, is_active=True).exists()

    @classmethod
    def get_user_committees(cls, user):
        """Get all committees that a user chairs."""
        if not user or not user.is_authenticated:
            return Committee.objects.none()
        return Committee.objects.filter(
            chairs__user=user, chairs__is_active=True
        ).distinct()

    @classmethod
    def get_committee_chairs(cls, committee):
        """Get all chair users for a specific committee."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        if not committee:
            return User.objects.none()
        return User.objects.filter(
            committee_chair_roles__committee=committee, committee_chair_roles__is_active=True
        ).distinct()


class SIGCommittee(models.Model):
    """
    Links a SIG (Group) to a Committee.

    This model creates the relationship between Special Interest Groups (represented as Django Groups)
    and Committees. Each SIG belongs to one committee.
    """

    group = models.OneToOneField(
        Group,
        on_delete=models.CASCADE,
        related_name="committee_membership",
        help_text="SIG (Group) that belongs to this committee",
    )
    committee = models.ForeignKey(
        Committee,
        on_delete=models.CASCADE,
        related_name="sigs",
        help_text="Committee this SIG belongs to",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SIG Committee Membership"
        verbose_name_plural = "SIG Committee Memberships"
        indexes = [
            models.Index(fields=["committee", "group"]),
        ]

    def __str__(self) -> str:
        return f"{self.group.name} - {self.committee.name}"


class UserRegistrationToken(models.Model):
    """
    One-time use token for user registration via QR code.

    Allows admins to create users with a token that can be used once
    to set up the user's password and complete account setup.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="registration_token",
        help_text="User account associated with this token",
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique token for registration",
    )
    used = models.BooleanField(
        default=False,
        help_text="Whether this token has been used",
    )
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this token was used",
    )
    expires_at = models.DateTimeField(
        help_text="When this token expires",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_registration_tokens",
        help_text="Admin user who created this token",
    )

    class Meta:
        db_table = "membership_user_registration_token"
        verbose_name = "User Registration Token"
        verbose_name_plural = "User Registration Tokens"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token", "used"]),
            models.Index(fields=["expires_at", "used"]),
        ]

    def __str__(self) -> str:
        return f"Registration token for {self.user.username} ({'used' if self.used else 'active'})"

    @classmethod
    def generate_token(cls) -> str:
        """Generate a secure random token."""
        return secrets.token_urlsafe(48)

    @classmethod
    def create_for_user(
        cls,
        user,
        created_by=None,
        expiration_days: int = 7,
    ) -> "UserRegistrationToken":
        """
        Create a registration token for a user.

        Args:
            user: User instance to create token for
            created_by: Admin user creating the token
            expiration_days: Number of days until token expires (default 7)

        Returns:
            UserRegistrationToken instance
        """
        token = cls.generate_token()
        expires_at = timezone.now() + timedelta(days=expiration_days)

        registration_token = cls.objects.create(
            user=user,
            token=token,
            expires_at=expires_at,
            created_by=created_by,
        )

        return registration_token

    def is_valid(self) -> bool:
        """Check if token is valid (not used and not expired)."""
        if self.used:
            return False
        if timezone.now() > self.expires_at:
            return False
        return True

    def mark_as_used(self) -> None:
        """Mark token as used."""
        self.used = True
        self.used_at = timezone.now()
        self.save(update_fields=["used", "used_at"])

    def get_registration_url(self) -> str:
        """Get the registration URL for this token."""
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        return f"{frontend_url}/register/{self.token}"
