"""The cookie ``/media/`` runs on outlives nothing the app runs on.

THE FAILING SEQUENCE THIS PINS. ``config.protected_media`` gates the vendor
prefixes on ``request.user.is_authenticated``, which a browser following an
``<a href="/media/...">`` satisfies with the Django session cookie and nothing
else — such a request carries no ``Authorization`` header. But the SPA runs on
the JWT: ``SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]`` is 30 days, while the session
cookie expires at Django's default ``SESSION_COOKIE_AGE`` (14 days) and is not
slid forward, because ``SESSION_SAVE_EVERY_REQUEST`` is left at ``False``.

So an operator signed in once, kept working for a month because every API call
carried a refreshed Bearer token, and from day 15 got a bare 403 on every
supplier agreement, purchase-order invoice, receipt and index card — with no
message, no redirect and no remedy short of signing out and back in. Nine media
prefixes depend on that cookie, up from zero before op-anonymous-read-posture,
so the blast radius is new even though the lifetime mismatch is not.

``auth_views.refresh_token`` now re-establishes the session when it mints an
access token. Its docstring records why the two other candidate fixes —
lengthening the cookie, or teaching the media gate to accept a JWT — were
rejected as posture changes rather than regression fixes.
"""

from __future__ import annotations

from django.core.files.base import ContentFile

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(django_user_model):
    """Somebody who can actually sign in.

    ``is_staff`` because ``User.can_login`` refuses anyone without it and
    without an active membership, and ``login_user`` enforces that — a user who
    cannot reach the login endpoint cannot exercise the sequence below.
    """
    return django_user_model.objects.create_user(
        username="zzqq-media-operator",
        password="zzqq-not-a-real-password",  # nosec B106
        is_staff=True,
    )


@pytest.fixture
def agreement(operator):
    """A real supplier agreement with real bytes under a gated prefix."""
    from inventory.models import Supplier, SupplierAgreement

    supplier = Supplier.objects.create(name="ZZQQ Lifetime Vendor", supplier_type="online")
    record = SupplierAgreement.objects.create(supplier=supplier, name="ZZQQ terms")
    record.document.save("zzqq-lifetime.pdf", ContentFile(b"ZZQQ-SECRET-LIFETIME-BODY"), save=True)
    yield record
    record.document.delete(save=False)


def _sign_in(client, operator):
    """Sign in the way the SPA does, and return the refresh token it keeps."""
    response = client.post(
        "/api/auth/login/",
        {"username": operator.username, "password": "zzqq-not-a-real-password"},
        format="json",
    )
    assert response.status_code == 200, response.data
    return response.data["refresh"]


@pytest.mark.integration
def test_a_refresh_restores_the_session_a_media_download_needs(agreement, operator):
    """REGRESSION. Fails before the fix: the refresh minted a token and left the
    lapsed session alone, so the download stayed 403."""
    client = APIClient()
    refresh = _sign_in(client, operator)

    # The session lapses while the refresh token stays valid — day 15 of 30.
    # Flushing is what expiry looks like to the next request: no usable session
    # cookie, so AuthenticationMiddleware resolves AnonymousUser.
    client.session.flush()
    client.cookies.pop("sessionid", None)
    assert client.get(agreement.document.url).status_code == 403

    refreshed = client.post("/api/auth/refresh/", {"refresh": refresh}, format="json")
    assert refreshed.status_code == 200
    assert refreshed.data["access"]

    allowed = client.get(agreement.document.url)
    assert allowed.status_code == 200
    body = b"".join(allowed.streaming_content) if allowed.streaming else allowed.content
    assert b"ZZQQ-SECRET-LIFETIME-BODY" in body


@pytest.mark.integration
def test_the_auth_request_endpoint_agrees_after_the_same_refresh(operator):
    """The nginx deployment goes through the subrequest, not through Django's
    media view, so it has to see the same restored session."""
    client = APIClient()
    refresh = _sign_in(client, operator)

    client.session.flush()
    client.cookies.pop("sessionid", None)
    assert client.get("/api/auth/media-access/").status_code == 403

    assert client.post("/api/auth/refresh/", {"refresh": refresh}, format="json").status_code == 200

    allowed = client.get("/api/auth/media-access/")
    assert 200 <= allowed.status_code < 300


@pytest.mark.integration
def test_a_refresh_with_no_session_support_still_returns_its_token(operator):
    """CONTROL: ScanTTY and curl hold a refresh token and no cookie jar.

    Renewing the session is a bonus for browsers; it must never become something
    the refresh can fail on.
    """
    signed_in = APIClient()
    refresh = _sign_in(signed_in, operator)

    tokens_only = APIClient()
    response = tokens_only.post("/api/auth/refresh/", {"refresh": refresh}, format="json")

    assert response.status_code == 200
    assert response.data["access"]


@pytest.mark.integration
def test_a_renewal_that_raises_does_not_fail_the_refresh(operator, monkeypatch):
    """REGRESSION. The renewal is a bonus; it must never cost the caller a token.

    ``django_login`` is not an in-memory call — ``cycle_key``/``flush`` write
    the session store and ``user_logged_in`` fires ``update_last_login``, which
    saves the user. Any of those can raise on ordinary transient trouble. It
    used to run inside ``refresh_token``'s ``except Exception`` with only
    ``get_user`` guarded, so the reply became 401 "Invalid refresh token" — and
    ``services/api.ts`` reads that as a failed refresh, clears localStorage and
    signs the operator out. A DB hiccup logged people out.
    """
    client = APIClient()
    refresh = _sign_in(client, operator)

    # The session has to be GONE for the renewal to reach `django_login` — with
    # one already naming this user the helper only touches its expiry. That is
    # also the state the whole fix exists for: day 15, session lapsed.
    client.session.flush()
    client.cookies.pop("sessionid", None)

    def explode(*args, **kwargs):
        raise RuntimeError("session store unavailable")

    monkeypatch.setattr("auth_views.django_login", explode)

    response = client.post("/api/auth/refresh/", {"refresh": refresh}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["access"]


@pytest.mark.integration
def test_a_renewal_that_raises_in_the_user_lookup_does_not_fail_it_either(operator, monkeypatch):
    """The same guarantee for the step the old guard DID cover.

    Kept as a pair with the case above so the two halves of the helper cannot
    drift apart: whichever line raises, the caller still gets its token.
    """
    client = APIClient()
    refresh = _sign_in(client, operator)

    def explode(self, token):
        raise RuntimeError("user lookup unavailable")

    monkeypatch.setattr(
        "rest_framework_simplejwt.authentication.JWTAuthentication.get_user", explode
    )

    response = client.post("/api/auth/refresh/", {"refresh": refresh}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["access"]


@pytest.mark.integration
def test_a_refresh_grants_no_media_access_to_a_caller_with_no_token(agreement):
    """CONTROL: the fix renews a session, it does not create one from nothing."""
    client = APIClient()

    rejected = client.post("/api/auth/refresh/", {"refresh": "not-a-token"}, format="json")
    assert rejected.status_code == 401

    assert client.get(agreement.document.url).status_code == 403


@pytest.mark.integration
def test_a_refused_download_names_a_remedy_and_leaks_nothing(agreement):
    """A bare 403 is the end of the road for whoever clicked the link.

    These are ordinary browser navigations, so no SPA error handler runs
    downstream — the body is the whole of what the person sees. It must offer a
    way in, and it must not echo what was asked for.
    """
    response = APIClient().get(agreement.document.url)

    assert response.status_code == 403
    body = response.content.decode()
    assert "Sign in" in body
    assert 'href="/"' in body

    # Nothing about the request: not the filename, not the prefix, not the
    # vendor whose paperwork it is.
    assert "zzqq-lifetime" not in body.lower()
    assert "supplier_agreements" not in body
    assert "ZZQQ Lifetime Vendor" not in body
    assert agreement.document.url not in body
