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

``auth_views.refresh_token`` SLIDES that cookie forward each time it mints an
access token — and creates no session, ever. The access token lives 7 days, so
a signed-in browser posts there at least weekly and the 14-day cookie never
reaches its expiry. The tests below walk that calendar, and pin the two things
the slide must NOT do: mint a session for a caller who has none (a refresh
token is not ``HttpOnly`` and must not buy the cookie Django admin runs on),
and touch a session belonging to somebody else.
"""

from __future__ import annotations

import datetime as dt

from django.contrib.sessions.models import Session
from django.core.files.base import ContentFile

import pytest
from freezegun import freeze_time
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

#: The calendar the failing sequence is told on. Day 0 sign-in, a weekly-ish
#: token refresh on day 10, and a download on day 16 — past the 14-day expiry
#: the original cookie carried.
DAY_ZERO = dt.datetime(2026, 3, 1, 9, 0, tzinfo=dt.timezone.utc)
REFRESH_DAY = DAY_ZERO + dt.timedelta(days=10)
PAST_ORIGINAL_EXPIRY = DAY_ZERO + dt.timedelta(days=16)


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
def other_operator(django_user_model):
    """A second signed-in user, for the session this must not touch."""
    return django_user_model.objects.create_user(
        username="zzqq-other-operator",
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


def _refresh(client, refresh):
    return client.post("/api/auth/refresh/", {"refresh": refresh}, format="json")


def _session_expiry(client):
    """The expire_date the session store holds for ``client``'s cookie."""
    key = client.cookies["sessionid"].value
    return Session.objects.get(session_key=key).expire_date


@pytest.mark.integration
def test_a_refresh_slides_the_session_a_media_download_needs(agreement, operator):
    """REGRESSION, on the calendar it happens on.

    Before the fix the day-10 refresh minted a token and left the cookie's
    14-day expiry alone, so the day-16 download was refused.
    """
    client = APIClient()

    with freeze_time(DAY_ZERO):
        refresh = _sign_in(client, operator)
        original_expiry = _session_expiry(client)

    with freeze_time(REFRESH_DAY):
        refreshed = _refresh(client, refresh)
        assert refreshed.status_code == 200, refreshed.data
        assert refreshed.data["access"]
        assert _session_expiry(client) > original_expiry

    with freeze_time(PAST_ORIGINAL_EXPIRY):
        allowed = client.get(agreement.document.url)
        assert allowed.status_code == 200
        body = b"".join(allowed.streaming_content) if allowed.streaming else allowed.content
        assert b"ZZQQ-SECRET-LIFETIME-BODY" in body


@pytest.mark.integration
def test_without_that_refresh_the_same_download_is_refused(agreement, operator):
    """CONTROL: it is the refresh doing the work, not the calendar being kind."""
    client = APIClient()

    with freeze_time(DAY_ZERO):
        _sign_in(client, operator)

    with freeze_time(PAST_ORIGINAL_EXPIRY):
        assert client.get(agreement.document.url).status_code == 403


@pytest.mark.integration
def test_the_auth_request_endpoint_agrees_after_the_same_refresh(operator):
    """The nginx deployment goes through the subrequest, not through Django's
    media view, so it has to see the same slid session."""
    client = APIClient()

    with freeze_time(DAY_ZERO):
        refresh = _sign_in(client, operator)

    with freeze_time(REFRESH_DAY):
        assert _refresh(client, refresh).status_code == 200

    with freeze_time(PAST_ORIGINAL_EXPIRY):
        allowed = client.get("/api/auth/media-access/")
        assert 200 <= allowed.status_code < 300


@pytest.mark.integration
def test_a_refresh_from_a_caller_with_no_session_creates_none(operator):
    """THE WIDENING THIS REVERTS, pinned so it cannot come back.

    A refresh token is not ``HttpOnly``, lives 30 days in ``localStorage`` and
    is cached on disk by ScanTTY. The session cookie is what ``config/urls.py``
    serves Django admin off. Minting the second from the first collapses two
    credential classes into one, so a cookie-less caller must come away with
    no session row, no ``Set-Cookie``, and an untouched ``last_login``.
    """
    signed_in = APIClient()
    refresh = _sign_in(signed_in, operator)

    operator.refresh_from_db()
    last_login_before = operator.last_login
    sessions_before = set(Session.objects.values_list("session_key", flat=True))

    tokens_only = APIClient()
    response = _refresh(tokens_only, refresh)

    assert response.status_code == 200
    assert response.data["access"]
    assert "sessionid" not in response.cookies
    assert set(Session.objects.values_list("session_key", flat=True)) == sessions_before

    operator.refresh_from_db()
    assert operator.last_login == last_login_before


@pytest.mark.integration
def test_a_session_belonging_to_somebody_else_is_left_alone(operator, other_operator):
    """A token proves who its holder is; it says nothing about whose cookie
    this browser is carrying. The mismatch is left exactly as it was — neither
    slid nor replaced."""
    client = APIClient()

    with freeze_time(DAY_ZERO):
        _sign_in(client, other_operator)
        their_key = client.cookies["sessionid"].value
        their_expiry = _session_expiry(client)

    tokens_only = APIClient()
    with freeze_time(DAY_ZERO):
        our_refresh = _sign_in(tokens_only, operator)

    with freeze_time(REFRESH_DAY):
        assert _refresh(client, our_refresh).status_code == 200

    assert client.cookies["sessionid"].value == their_key
    assert _session_expiry(client) == their_expiry
    assert Session.objects.get(session_key=their_key).get_decoded()["_auth_user_id"] == str(
        other_operator.pk
    )


@pytest.mark.integration
def test_a_slide_that_raises_does_not_fail_the_refresh(operator, monkeypatch):
    """REGRESSION. The renewal is a bonus; it must never cost the caller a token.

    Writing a session is a write, and writes fail: contention, a transient
    ``OperationalError``, a read-only replica. This used to run inside
    ``refresh_token``'s ``except Exception`` with only ``get_user`` guarded, so
    the reply became 401 "Invalid refresh token" — and ``services/api.ts``
    reads that as a failed refresh, clears localStorage and signs the operator
    out. A DB hiccup logged people out.
    """
    client = APIClient()
    refresh = _sign_in(client, operator)

    def explode(*args, **kwargs):
        raise RuntimeError("session store unavailable")

    monkeypatch.setattr(
        "django.contrib.sessions.backends.db.SessionStore.set_expiry", explode, raising=False
    )

    response = _refresh(client, refresh)

    assert response.status_code == 200, response.data
    assert response.data["access"]


@pytest.mark.integration
def test_a_renewal_that_raises_in_the_user_lookup_does_not_fail_it_either(operator, monkeypatch):
    """The same guarantee for the other step the helper takes.

    Kept as a pair with the case above so the two halves cannot drift apart:
    whichever line raises, the caller still gets its token.
    """
    client = APIClient()
    refresh = _sign_in(client, operator)

    def explode(self, token):
        raise RuntimeError("user lookup unavailable")

    monkeypatch.setattr(
        "rest_framework_simplejwt.authentication.JWTAuthentication.get_user", explode
    )

    response = _refresh(client, refresh)

    assert response.status_code == 200, response.data
    assert response.data["access"]


@pytest.mark.integration
def test_a_refresh_grants_no_media_access_to_a_caller_with_no_token(agreement):
    """CONTROL: a rejected refresh opens nothing."""
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
