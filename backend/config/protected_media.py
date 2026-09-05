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
  vendor prefixes below carry ``auth_request`` pointing at
  :func:`media_access_check`. The path a client uses does not change, so no
  payload, no stored ``file_url``, and no consumer moves.
* The check is a session check, and it has to be: a browser following
  ``<a href="/media/...">`` sends cookies and no ``Authorization`` header, so
  the JWT the SPA runs on never reaches this request.
  ``auth_views.login_user`` establishes that cookie alongside the JWT.
* THE TWO LIFETIMES ARE RECONCILED IN ``auth_views.refresh_token``, and that is
  load-bearing rather than incidental. The session cookie expires at Django's
  default ``SESSION_COOKIE_AGE`` (14 days) and is not slid forward
  (``SESSION_SAVE_EVERY_REQUEST`` is left at ``False``), while
  ``SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]`` is 30 days — so an operator whose
  API calls kept working would have hit a bare 403 on every invoice from day
  15. ``refresh_token`` now renews the session when it mints an access token;
  its docstring records which two alternative fixes were rejected and why.
  ``config/tests/test_media_session_lifetime.py`` exercises that sequence.
* A REFUSAL CARRIES A REMEDY. Both servers answer 403 with a small page that
  says how to get in, because these are ordinary browser navigations and a
  stock 403 body is the end of the road for whoever clicked. What it must
  never carry is what was asked for — see :func:`_forbidden_with_remedy`.
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

THE UNIT OF THAT DERIVATION IS AN UPLOAD FIELD, NOT A URL PREFIX, and the first
pass got that backwards: it enumerated the prefixes it had already found rather
than asking where a vendor document can be STORED, and so missed five roots that
hold invoices — two of them fed by unfiltered inbound mail. Every ``upload_to``
under ``backend/`` is now classified, and
``config/tests/test_upload_field_classification.py`` fails the build on a new
one until somebody says which side of this list it belongs on.
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
#: * ``third_party_work_orders/`` — ``maintenance_orders
#:   .ThirdPartyWorkOrderAttachment.file``, whose ``KIND_CHOICES`` are Invoice,
#:   Field Service Report, Photo, Quote, Paper Form, Other. Its ``upload_to`` is
#:   the callable ``_attachment_upload_path``, which is why a walk over string
#:   literals alone would not have found it.
#: * ``inventory/maintenance_records/`` — ``inventory.MaintenanceRecord
#:   .attachment``, "Invoice PDF, receipt photo, etc.", on a model that also
#:   carries a ``vendor`` FK, a ``cost`` and an ``invoice_number``.
#: * ``work_orders/attachments/`` — ``inventory.WorkOrderAttachment.file``,
#:   which exists (its own docstring) because "a supplier receipt, a datasheet
#:   page, a torque spec, a photo of the nameplate" had nowhere to live.
#: * ``work_orders/submissions/`` — ``inventory.WorkOrderSubmission
#:   .attachment``, "the raw PDF attachment as received from the email". This is
#:   UNFILTERED INBOUND MAIL through the Postmark webhook: whatever a vendor
#:   emails to that address is stored here verbatim, so the contents cannot be
#:   narrowed by argument.
#: * ``work_orders/scans/`` — ``inventory.WorkOrder.completed_scan``, a
#:   completed paper work order arriving down that same inbound path, carrying
#:   the job's material costs.
VENDOR_MEDIA_PREFIXES = (
    "supplier_agreements/",
    "purchase_orders/attachments/",
    "work_orders/receipts/",
    "index_cards/",
    "third_party_work_orders/",
    "inventory/maintenance_records/",
    "work_orders/attachments/",
    "work_orders/submissions/",
    "work_orders/scans/",
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
    """``auth_request`` target for nginx: 204 to a signed-in caller, else 403.

    That is the whole decision this view makes. WHICH paths are gated is decided
    by the ``location ^~`` blocks in ``nginx/templates/default.conf.template``,
    which is why nothing here reads a path: nginx only issues the subrequest for
    a prefix it has already chosen to protect.

    ``never_cache`` because a cached allow here is an open door for everybody
    behind the same proxy;
    ``config/tests/test_protected_media.py::test_the_auth_request_endpoint_answers_the_way_nginx_needs``
    asserts both answers and the ``no-cache`` header.
    """
    if request.user.is_authenticated:
        return HttpResponse(status=204)
    return HttpResponseForbidden()


#: What a refused ``/media/`` request gets INSTEAD OF A BLANK WALL.
#:
#: These URLs are followed by a browser from an ``<a href>``, so there is no
#: SPA error handler downstream — whatever the server returns is the whole of
#: what the person sees. A stock 403 body names no remedy, and the operator
#: whose session lapsed under them has done nothing wrong.
#:
#: IT NAMES NO PATH, NO FILENAME, NO PREFIX AND NO VENDOR. The reader already
#: knows what they clicked; anyone else reaching this page must learn nothing
#: from it, and a refusal that echoes the request is a refusal that leaks.
FORBIDDEN_REMEDY_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Sign in required</title></head>
<body>
<h1>Sign in required</h1>
<p>This document is only available to signed-in members.</p>
<p><a href="/">Sign in</a>, then follow the link again.</p>
</body></html>
"""


def _forbidden_with_remedy():
    """403 with :data:`FORBIDDEN_REMEDY_HTML`."""
    return HttpResponseForbidden(FORBIDDEN_REMEDY_HTML, content_type="text/html; charset=utf-8")


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

    Refuses with :func:`_forbidden_with_remedy` rather than a bare 403, because
    "two servers, one rule" has to cover what the refused caller is TOLD as
    well as whether they are let in; the nginx half returns the same page from
    its ``error_page 401 403`` for these prefixes.
    """
    if is_vendor_media(path) and not request.user.is_authenticated:
        return _forbidden_with_remedy()
    return django_static_serve(request, path, document_root=settings.MEDIA_ROOT)
