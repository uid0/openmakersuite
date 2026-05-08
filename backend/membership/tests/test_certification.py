"""Tests for the Certification + UserCertification models and helpers
(gh ForgeKey expansion, Phase 1+2)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError

import pytest

from membership.models import Certification, UserCertification
from membership.utils import is_certified, user_active_certifications

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def sig():
    return Group.objects.create(name="Wood Shop SIG")


@pytest.fixture
def cert(sig):
    return Certification.objects.create(name="Wood Shop Operator", slug="wso", sig=sig)


@pytest.fixture
def user():
    return User.objects.create_user(username="u", email="u@example.com", password="x" * 24)


class TestCertificationModel:
    def test_str_includes_sig_name(self, sig, cert):
        assert sig.name in str(cert)

    def test_default_is_active(self, sig):
        c = Certification.objects.create(name="Foo", slug="foo", sig=sig)
        assert c.is_active is True

    def test_unique_slug(self, sig, cert):
        with pytest.raises(IntegrityError):
            Certification.objects.create(name="Other", slug=cert.slug, sig=sig)


class TestUserCertificationGrant:
    def test_grant_is_active_by_default(self, user, cert):
        grant = UserCertification.objects.create(user=user, certification=cert)
        assert grant.is_active is True
        assert grant.revoked_at is None

    def test_only_one_active_grant_per_pair(self, user, cert):
        UserCertification.objects.create(user=user, certification=cert)
        with pytest.raises(IntegrityError):
            UserCertification.objects.create(user=user, certification=cert)

    def test_revoked_grant_allows_new_grant(self, user, cert):
        first = UserCertification.objects.create(user=user, certification=cert)
        first.revoke(by=user)
        # New active grant succeeds because the partial index ignores
        # revoked rows.
        UserCertification.objects.create(user=user, certification=cert)


class TestRevoke:
    def test_revoke_marks_revoked_at_and_by(self, user, cert):
        revoker = User.objects.create_user(username="rev", email="r@example.com", password="x" * 24)
        grant = UserCertification.objects.create(user=user, certification=cert)
        grant.revoke(by=revoker, notes="failed re-cert exam")
        grant.refresh_from_db()
        assert grant.revoked_at is not None
        assert grant.revoked_by == revoker
        assert "failed re-cert" in grant.notes
        assert grant.is_active is False

    def test_revoke_idempotent(self, user, cert):
        grant = UserCertification.objects.create(user=user, certification=cert)
        grant.revoke(by=user)
        first = grant.revoked_at
        grant.revoke(by=user)
        grant.refresh_from_db()
        assert grant.revoked_at == first


class TestIsCertified:
    def test_active_grant_is_certified(self, user, cert):
        UserCertification.objects.create(user=user, certification=cert)
        assert is_certified(user, cert) is True

    def test_revoked_grant_not_certified(self, user, cert):
        grant = UserCertification.objects.create(user=user, certification=cert)
        grant.revoke(by=user)
        assert is_certified(user, cert) is False

    def test_inactive_certification_not_certified(self, user, cert):
        UserCertification.objects.create(user=user, certification=cert)
        cert.is_active = False
        cert.save()
        assert is_certified(user, cert) is False

    def test_anonymous_or_none_returns_false(self, cert):
        from django.contrib.auth.models import AnonymousUser

        assert is_certified(AnonymousUser(), cert) is False
        assert is_certified(None, cert) is False
        assert is_certified(None, None) is False


class TestUserActiveCertifications:
    def test_returns_active_grants_only(self, user, cert, sig):
        other_cert = Certification.objects.create(name="Sander", slug="sander", sig=sig)
        UserCertification.objects.create(user=user, certification=cert)
        revoked = UserCertification.objects.create(user=user, certification=other_cert)
        revoked.revoke(by=user)
        result = list(user_active_certifications(user))
        assert cert in result
        assert other_cert not in result

    def test_empty_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        assert list(user_active_certifications(AnonymousUser())) == []
