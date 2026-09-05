"""Vendor paperwork under ``MEDIA_ROOT`` is not public, and nginx serves it.

    "Vendor names should not be public, same with Vendor Pricing. They should
    always be behind user auth."  — the captain, op-anonymous-read-posture

A ``FileField`` URL is answered by the WEB SERVER, not by Django, so no
``permission_classes`` change reaches it: ``nginx/templates/default.conf.template``
had one ``location /media/ { alias /app/media/; }`` block that served every
upload to anyone, cached ``public`` for seven days. A signed supplier agreement
and a purchase-order invoice both live there. Verified by running nginx against
that block: HTTP 200, full body.

THE SHAPE OF THE FIX, and why it is this shape:

* nginx keeps serving the bytes — sendfile is why the files are there — but the
  four vendor prefixes below carry ``auth_request`` pointing at
  :func:`media_access_check`. The path a client uses does not change, so no
  payload, no stored ``file_url``, and no consumer moves.
* The check is a session check, and that works because ``auth_views.login_user``
  already establishes a Django session cookie alongside the JWT (its own
  docstring says the cookie exists so "the same credentials also grant access").
  A browser following ``<a href="/media/...">`` sends that cookie; an anonymous
  visitor sends nothing and gets 403 from nginx before a byte is read.
* Django's own ``static()`` media serving — development, and anything that
  reaches ``/media/`` without nginx in front — is replaced by
  :func:`serve_media`, which applies the SAME prefix list. Two servers with one
  rule between them is the only version of this that can be tested; a gate that
  exists only in an nginx template is a gate nothing in CI can exercise.

WHY A PREFIX LIST AND NOT "GATE ALL MEDIA". Item photos, QR codes, MSDS sheets,
location-problem snapshots and asset manuals are on the anonymous scan path or
are safety information, and closing them would break the flow the printed QR
codes exist for. The list below is derived from the captain's sentence, entry by
entry, and each entry says which model writes there.
"""

from __future__ import annotations

import posixpath

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.cache import never_cache
from django.views.static import serve as django_static_serve

#: Media prefixes that hold vendor identity or vendor money. Paths are relative
#: to ``MEDIA_ROOT`` (equivalently, what follows ``MEDIA_URL``).
#:
#: * ``supplier_agreements/`` — ``inventory.SupplierAgreement.document``, the
#:   scanned contract or standing quote. Names the vendor and states the terms;
#:   the captain's decision names agreements outright.
#: * ``purchase_orders/attachments/`` — ``reorder_queue.PurchaseOrderAttachment
#:   .file``, where supplier invoices are filed. Named outright too.
#: * ``work_orders/receipts/`` — ``inventory.WorkOrderMaterialUsage
#:   .receipt_image``, "photo of the receipt backing an out-of-pocket purchase".
#:   A receipt photograph is a vendor's name and their prices, in an image; the
#:   fact that it is a picture rather than a column changes nothing about what
#:   it discloses.
#: * ``index_cards/`` — the batch PDFs ``IndexCardRenderer.render_batch_to_storage``
#:   persists for ``IndexCardBatchGenerateView``. That view is authenticated and
#:   therefore prints the lead-time lines, so the resulting file carries them
#:   even though the endpoint that made it is closed. A generated artefact
#:   inherits the audience of its contents, not of its generator.
VENDOR_MEDIA_PREFIXES = (
    "supplier_agreements/",
    "purchase_orders/attachments/",
    "work_orders/receipts/",
    "index_cards/",
)


def is_vendor_media(relative_path: str) -> bool:
    """Whether ``relative_path`` (relative to ``MEDIA_ROOT``) is vendor paperwork.

    Normalised first so ``supplier_agreements/../supplier_agreements/x.pdf`` and
    a leading slash cannot walk around the prefix test.
    """
    normalised = posixpath.normpath("/" + relative_path.replace("\\", "/")).lstrip("/")
    return any(normalised.startswith(prefix) for prefix in VENDOR_MEDIA_PREFIXES)


@never_cache
def media_access_check(request):
    """``auth_request`` target for nginx: 200 to a signed-in caller, else 403.

    nginx passes the original path in ``X-Original-URI``. When that header names
    a path OUTSIDE :data:`VENDOR_MEDIA_PREFIXES` this still answers 200 — the
    prefix decision belongs to the nginx ``location`` blocks, and answering 403
    for a public file merely because a misconfigured block asked would take down
    item photos. The header is checked, not trusted: it can only ever widen an
    answer to 200 for a path nginx had already decided to protect, never narrow
    one.

    ``never_cache`` because a cached 200 here is an open door for everybody
    behind the same proxy.
    """
    if request.user.is_authenticated:
        return HttpResponse(status=204)
    return HttpResponseForbidden()


@never_cache
def serve_media(request, path):
    """Django's development media serving, with the vendor prefixes gated.

    Replaces the bare ``static(MEDIA_URL, document_root=MEDIA_ROOT)`` that
    ``config/urls.py`` used to add under ``DEBUG``. Registered unconditionally
    now, because "the rule only exists when DEBUG is on" is how the development
    server and the tests came to disagree with production about who may read a
    supplier's invoice.

    In production nginx answers ``/media/`` before Django sees it, so this is the
    second of two implementations of one rule — both reading
    :data:`VENDOR_MEDIA_PREFIXES`, and both exercised:
    ``config/tests/test_protected_media.py`` requests through this one and
    asserts the nginx template gates the same prefixes.
    """
    if is_vendor_media(path) and not request.user.is_authenticated:
        return HttpResponseForbidden()
    return django_static_serve(request, path, document_root=settings.MEDIA_ROOT)
