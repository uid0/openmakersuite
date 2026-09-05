"""Vendor paperwork under ``/media/`` is not readable without a login.

TWO SERVERS, ONE RULE, AND BOTH ARE CHECKED HERE. In production nginx answers
``/media/`` before Django sees it, so the Python half of this cannot prove the
deployment is closed — that is exactly the trap the brief names ("a file served
by the web server rather than the application will not be closed by a Django
permission change"). So this module does two different things:

1. exercises :func:`config.protected_media.serve_media` with real requests —
   the code path every deployment without nginx in front uses, including the
   development server and this test client; and
2. asserts that ``nginx/templates/default.conf.template`` gates the SAME prefix
   list, reading the list from :data:`~config.protected_media.VENDOR_MEDIA_PREFIXES`
   rather than restating it, so a prefix added in Python and forgotten in nginx
   fails here.

The deployment itself was additionally verified out of band by running nginx
with that template in front of a real Django process: anonymous requests to all
four prefixes answered 403 and a signed-in session answered 200 with the file's
bytes, while ``/media/inventory/qrcodes/`` stayed 200 for everyone. That
transcript is in the PR body; what CI can re-run on its own is below.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.core.files.base import ContentFile

import pytest
from rest_framework.test import APIClient

from config.protected_media import VENDOR_MEDIA_PREFIXES, is_vendor_media

NGINX_TEMPLATE = (
    Path(__file__).resolve().parents[3] / "nginx" / "templates" / "default.conf.template"
)


@pytest.fixture
def agreement_document(db):
    """A real supplier agreement with a real file on disk under MEDIA_ROOT."""
    from inventory.models import Supplier, SupplierAgreement

    supplier = Supplier.objects.create(name="ZZQQ Paperwork Vendor", supplier_type="online")
    agreement = SupplierAgreement.objects.create(supplier=supplier, name="ZZQQ terms")
    agreement.document.save(
        "zzqq-protected-media.pdf", ContentFile(b"ZZQQ-SECRET-AGREEMENT-BODY"), save=True
    )
    yield agreement
    agreement.document.delete(save=False)


@pytest.mark.integration
def test_an_anonymous_caller_cannot_read_a_supplier_agreement(agreement_document):
    response = APIClient().get(agreement_document.document.url)
    assert response.status_code == 403
    assert b"ZZQQ-SECRET-AGREEMENT-BODY" not in response.content


@pytest.mark.integration
def test_a_signed_in_caller_can(agreement_document, django_user_model):
    """CONTROL: the file is gated, not withdrawn.

    ``force_login`` rather than DRF's ``force_authenticate``, and the difference
    is the point: ``/media/`` is answered by a plain Django view, so the user
    comes from the SESSION that ``AuthenticationMiddleware`` resolves — the same
    cookie ``login_user`` sets and a browser sends when it follows
    ``<a href="/media/...">``. ``force_authenticate`` only ever reaches a DRF
    view and would prove nothing about this path.
    """
    user = django_user_model.objects.create_user(
        username="zzqq-reader", password="zzqq-not-a-real-password"  # nosec B106
    )
    client = APIClient()
    client.force_login(user)

    response = client.get(agreement_document.document.url)
    assert response.status_code == 200
    body = b"".join(response.streaming_content) if response.streaming else response.content
    assert b"ZZQQ-SECRET-AGREEMENT-BODY" in body


@pytest.mark.integration
def test_public_media_is_untouched(db, tmp_path, settings):
    """The anonymous scan path reads item photos and QR codes out of the same
    tree. Closing those would break the flow the printed codes exist for."""
    settings.MEDIA_ROOT = tmp_path
    public = tmp_path / "inventory" / "qrcodes"
    public.mkdir(parents=True)
    (public / "item.png").write_bytes(b"ZZQQ-PUBLIC-QR")

    response = APIClient().get("/media/inventory/qrcodes/item.png")
    assert response.status_code == 200
    body = b"".join(response.streaming_content) if response.streaming else response.content
    assert body == b"ZZQQ-PUBLIC-QR"


@pytest.mark.unit
@pytest.mark.parametrize(
    "path,protected",
    [
        ("supplier_agreements/a.pdf", True),
        ("purchase_orders/attachments/2026/09/i.pdf", True),
        ("work_orders/receipts/2026/09/r.jpg", True),
        ("index_cards/batch.pdf", True),
        ("inventory/qrcodes/item.png", False),
        ("inventory/images/item.jpg", False),
        ("inventory/msds/sheet.pdf", False),
        ("location_problems/2026/09/photo.jpg", False),
        # Traversal must not walk out of a protected prefix, nor into one.
        ("supplier_agreements/../inventory/qrcodes/item.png", False),
        ("inventory/qrcodes/../../supplier_agreements/a.pdf", True),
        ("/supplier_agreements/a.pdf", True),
    ],
)
def test_the_prefix_test_normalises_before_deciding(path, protected):
    assert is_vendor_media(path) is protected


@pytest.mark.unit
def test_the_nginx_template_gates_every_protected_prefix():
    """The half of the rule Python cannot enforce.

    Reads the prefix list from the Python module rather than restating it, so
    adding a prefix in one place and not the other fails here rather than in
    production.
    """
    template = NGINX_TEMPLATE.read_text()

    for prefix in VENDOR_MEDIA_PREFIXES:
        location = re.search(
            r"location\s+\^~\s+/media/" + re.escape(prefix) + r"\s*\{(.*?)\n    \}",
            template,
            re.S,
        )
        assert location, f"nginx serves /media/{prefix} with no dedicated location block"
        assert "auth_request /_vendor_media_auth;" in location.group(
            1
        ), f"/media/{prefix} has a location block but no auth_request"
        assert "expires 7d" not in location.group(
            1
        ), f"/media/{prefix} would be cached publicly by an intermediary"

    subrequest = re.search(r"location\s+=\s+/_vendor_media_auth\s*\{(.*?)\n    \}", template, re.S)
    assert subrequest, "the auth_request target is not defined"
    body = subrequest.group(1)
    assert "internal;" in body, "the auth_request target is directly reachable"
    assert "/api/auth/media-access/" in body, "the auth_request target does not reach Django"


@pytest.mark.integration
def test_the_auth_request_endpoint_answers_the_way_nginx_needs(db, django_user_model):
    """nginx treats 2xx as allow and 401/403 as deny."""
    anonymous = APIClient()
    assert anonymous.get("/api/auth/media-access/").status_code == 403

    user = django_user_model.objects.create_user(
        username="zzqq-subrequest", password="zzqq-not-a-real-password"  # nosec B106
    )
    signed_in = APIClient()
    signed_in.force_login(user)
    allowed = signed_in.get("/api/auth/media-access/")
    assert 200 <= allowed.status_code < 300
    # A cached allow would open the door for every caller behind the proxy.
    assert "no-cache" in allowed.headers.get("Cache-Control", "")
