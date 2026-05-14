"""
Regression tests for the membership User admin.

The UserAdmin previously extended ``admin.ModelAdmin`` directly, which left
it without the UserCreationForm plumbing or a separate ``add_fieldsets``.
That broke ``/admin/membership/user/add/``:

  * the create form had no password1/password2 widgets so passwords would
    have been stored as plaintext, and
  * the change-form fieldset was reused on the add page, which rendered M2M
    widgets (groups, user_permissions) against a not-yet-saved User instance
    and 500'd on submit.

This test suite locks down both the GET and the POST path against the
fixed admin so we don't regress.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

import pytest

User = get_user_model()


@pytest.fixture
def admin_client(db):
    admin = User.objects.create_superuser(
        username="user-admin-superuser",
        email="admin@example.com",
        password="adm1n-pw-not-used-by-test",  # pragma: allowlist secret
    )
    c = Client()
    c.force_login(admin)
    return c


@pytest.mark.django_db
class TestUserAdminAdd:
    def test_add_page_renders(self, admin_client):
        resp = admin_client.get(reverse("admin:membership_user_add"))
        assert resp.status_code == 200
        # The add fieldset should be username + password1 + password2 + a few
        # personal-info fields. M2M widgets must NOT appear on the add page,
        # since the row has no primary key yet.
        body = resp.content.decode()
        assert "password1" in body
        assert "password2" in body
        assert "user_permissions" not in body, "M2M field leaked onto add page"
        assert 'name="groups"' not in body, "M2M field leaked onto add page"

    def test_post_creates_user_with_hashed_password(self, admin_client):
        resp = admin_client.post(
            reverse("admin:membership_user_add"),
            data={
                "username": "newmember",
                "password1": "S3cure-pw-please",  # pragma: allowlist secret
                "password2": "S3cure-pw-please",  # pragma: allowlist secret
                "email": "new@example.com",
                "first_name": "New",
                "last_name": "Member",
                "_save": "Save",
            },
        )
        # Successful add redirects to the change-list (or change-page in
        # newer Django versions). 200 with a form means validation failed.
        assert resp.status_code in (301, 302), (
            f"add did not redirect; status={resp.status_code} " f"body={resp.content[:600]!r}"
        )
        u = User.objects.get(username="newmember")
        assert u.email == "new@example.com"
        # Password must be hashed, never plaintext.
        assert u.password != "S3cure-pw-please"  # pragma: allowlist secret
        assert u.check_password("S3cure-pw-please")  # pragma: allowlist secret

    def test_post_creates_registration_token(self, admin_client):
        """save_model still auto-creates a UserRegistrationToken on add."""
        admin_client.post(
            reverse("admin:membership_user_add"),
            data={
                "username": "tokenuser",
                "password1": "S3cure-pw-please",  # pragma: allowlist secret
                "password2": "S3cure-pw-please",  # pragma: allowlist secret
                "email": "tokenuser@example.com",
                "first_name": "Token",
                "last_name": "User",
                "_save": "Save",
            },
        )
        u = User.objects.get(username="tokenuser")
        # The token should have been auto-created by save_model.
        assert hasattr(u, "registration_token")
        assert u.registration_token.user_id == u.id
        assert not u.registration_token.used

    def test_post_rejects_mismatched_passwords(self, admin_client):
        resp = admin_client.post(
            reverse("admin:membership_user_add"),
            data={
                "username": "badpw",
                "password1": "alpha",
                "password2": "beta",
                "email": "badpw@example.com",
                "_save": "Save",
            },
        )
        # Form validation error stays on the add page with status 200.
        assert resp.status_code == 200
        assert not User.objects.filter(username="badpw").exists()

    def test_add_fieldsets_have_no_m2m(self):
        """add_fieldsets must not contain groups or user_permissions, which
        Django cannot render against a not-yet-saved User."""
        from membership.admin import UserAdmin

        flattened = []
        for _label, opts in UserAdmin.add_fieldsets:
            flattened.extend(opts.get("fields", ()))
        assert "groups" not in flattened
        assert "user_permissions" not in flattened
        # Sanity: the create form must include the password fields so the
        # password gets hashed by Django's setter rather than stored raw.
        assert "password1" in flattened
        assert "password2" in flattened
