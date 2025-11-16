"""
Models for membership management.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group
from django.db import models


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
        return Group.objects.filter(
            sig_admins__user=user, sig_admins__is_active=True
        ).distinct()

    @classmethod
    def get_sig_admins(cls, group):
        """Get all admin users for a specific SIG."""
        if not group:
            return settings.AUTH_USER_MODEL.objects.none()
        return settings.AUTH_USER_MODEL.objects.filter(
            sig_admin_roles__group=group, sig_admin_roles__is_active=True
        ).distinct()
