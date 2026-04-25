"""Models for the maker box (personal storage bin) system."""

from __future__ import annotations

from django.db import models


class MakerBox(models.Model):
    """A physical personal storage bin assigned (or assignable) to a member.

    Bins are identified by a stable bin_id printed on the physical container
    (e.g. ``PSB-007``). When a member rents a bin we record their WHMCS
    username plus the name we'll print on the label, and cache the WHMCS
    membership expiry so the scan endpoint can compute a status without
    re-hitting WHMCS for every read.
    """

    STATUS_VALID = "valid"
    STATUS_GRACE = "grace"
    STATUS_EXPIRED = "expired"
    STATUS_UNASSIGNED = "unassigned"
    STATUS_UNKNOWN = "unknown"

    STATUS_CHOICES = [
        (STATUS_VALID, "Valid"),
        (STATUS_GRACE, "Grace"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_UNASSIGNED, "Unassigned"),
        (STATUS_UNKNOWN, "Unknown"),
    ]

    bin_id = models.CharField(max_length=20, unique=True)
    assigned_username = models.CharField(max_length=64, blank=True)
    first_name = models.CharField(max_length=64, blank=True)
    last_name = models.CharField(max_length=64, blank=True)
    email = models.EmailField(blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_UNASSIGNED,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["bin_id"]
        verbose_name = "Maker box"
        verbose_name_plural = "Maker boxes"

    def __str__(self) -> str:
        if self.assigned_username:
            return f"{self.bin_id} ({self.assigned_username})"
        return self.bin_id

    @property
    def display_name(self) -> str:
        parts = [self.first_name, self.last_name]
        joined = " ".join(p for p in parts if p)
        return joined or self.assigned_username
