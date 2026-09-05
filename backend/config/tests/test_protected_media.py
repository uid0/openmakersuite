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

THE NGINX HALF PARSES, IT DOES NOT GREP. It used to search the template's raw
text for ``location ^~ /media/<prefix>``, ``auth_request`` and the absence of
``expires 7d`` — this repo's own named test anti-pattern, and defeated by any
of: a ``${VAR}`` in the prefix, a differently indented closing brace, or an
``expires 7d`` inherited from a wider block. ``config/tests/nginx_config.py``
renders the ``${VAR}`` substitution and parses the result into location blocks
and directives, and the assertions below ask that model which block nginx WOULD
SELECT for a real URI and what caching is EFFECTIVE there. Where an nginx binary
is on PATH the rendered config is additionally handed to ``nginx -t``.

The deployment itself was additionally verified out of band by running nginx
with that template in front of a real Django process: anonymous requests to the
protected prefixes answered 403 and a signed-in session answered 200 with the
file's bytes, while ``/media/inventory/qrcodes/`` stayed 200 for everyone. That
transcript is in the PR body; what CI can re-run on its own is below.
"""

from __future__ import annotations

import contextlib
import http.server
import re
import shutil
import socket
import subprocess  # nosec B404 — `nginx -t` on a test-owned rendered config
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.core.files.base import ContentFile

import pytest
from rest_framework.test import APIClient

from config.protected_media import (
    FORBIDDEN_REMEDY_HTML,
    REAUTH_PATH,
    VENDOR_MEDIA_PREFIXES,
    is_vendor_media,
)
from config.tests.nginx_config import parse, render, servers

NGINX_TEMPLATE = (
    Path(__file__).resolve().parents[3] / "nginx" / "templates" / "default.conf.template"
)

#: What the container's entrypoint substitutes. Values are stand-ins; only their
#: presence matters, so a prefix that ever became ``${VAR}``-templated resolves
#: to a concrete path here rather than silently failing to match.
TEMPLATE_VARIABLES = {
    "LETSENCRYPT_DOMAINS": "oms.test",
    "SENTRY_HOST": "sentry.test",
}

#: nginx treats these as "do not cache"; anything else is a positive lifetime.
NON_CACHING_EXPIRES = {"-1", "off", "epoch"}

#: The named location every gated prefix routes its 401/403 to, so a refused
#: browser navigation gets a remedy rather than nginx's stock body.
REMEDY_LOCATION = "@vendor_media_denied"


def _tls_server():
    """The ``server`` block that serves the app (the plain-80 one only redirects)."""
    root = parse(render(NGINX_TEMPLATE.read_text(), TEMPLATE_VARIABLES))
    for server in servers(root):
        if server.locations and any(
            loc.args and loc.args[-1] == "/media/" for loc in server.locations
        ):
            return server
    raise AssertionError("no server block in the nginx template serves /media/")


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
        ("third_party_work_orders/42/invoice.pdf", True),
        ("inventory/maintenance_records/invoice.pdf", True),
        ("work_orders/attachments/2026/09/receipt.jpg", True),
        ("work_orders/submissions/2026/09/inbound.pdf", True),
        ("work_orders/scans/2026/09/completed.pdf", True),
        # The gated roots are siblings of open ones under the same first
        # segment, so the prefix test has to be doing more than a first-segment
        # comparison for either answer to be right.
        ("work_order_photos/2026/09/photo.jpg", False),
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

    Asks the parsed model which location block nginx SELECTS for a real file
    under each prefix, and what ``auth_request`` is effective there. Selection
    rather than presence is the point: a block that exists but loses the match
    (a longer sibling prefix, a regex location declared earlier) gates nothing,
    and a text search cannot tell the two apart.

    Reads the prefix list from the Python module rather than restating it, so
    adding a prefix in one place and not the other fails here rather than in
    production.
    """
    server = _tls_server()

    for prefix in VENDOR_MEDIA_PREFIXES:
        uri = f"/media/{prefix}zzqq-probe.pdf"
        location = server.match_location(uri)
        assert location is not None, f"nginx matches no location at all for {uri}"

        auth = location.effective("auth_request")
        assert auth, (
            f"nginx serves {uri} from `location {' '.join(location.args)}`, which "
            "has no effective auth_request — the file is public."
        )
        assert [d.value for d in auth] != ["off"], f"{uri} switches auth_request off"

        target = auth[0].value
        subrequest = server.match_location(target)
        assert (
            subrequest is not None
        ), f"{uri} points auth_request at {target}, which is not defined"
        assert subrequest.declared("internal"), f"{target} is reachable directly by a client"
        proxies = subrequest.effective("proxy_pass")
        assert proxies and proxies[0].value.endswith(
            "/api/auth/media-access/"
        ), f"{target} does not reach the Django gate"


def _one_line(html: str) -> str:
    """``html`` with the whitespace BETWEEN tags collapsed away."""
    return re.sub(r">\s+<", "><", html.strip())


@pytest.mark.unit
def test_every_protected_prefix_refuses_with_a_remedy():
    """The nginx half of "a refusal carries a remedy".

    These URLs are followed by a browser from an ``<a href>``, so nginx's stock
    403 body is the whole of what the person sees — there is no SPA error
    handler downstream. Without this, copying a block for a tenth prefix and
    dropping its ``error_page`` line passes every other check here while giving
    that prefix a blank wall.

    ``config.protected_media.serve_media`` is the same rule on the other
    server, and ``test_media_session_lifetime.py`` covers it; this is the half
    that was asserted nowhere.
    """
    server = _tls_server()

    for prefix in VENDOR_MEDIA_PREFIXES:
        uri = f"/media/{prefix}zzqq-probe.pdf"
        location = server.match_location(uri)
        assert location is not None

        handlers = location.effective("error_page")
        assert handlers, f"a refused {uri} gets nginx's stock 403 body — no remedy, no link"
        named = [d.args[-1] for d in handlers]
        assert REMEDY_LOCATION in named, (
            f"{uri} handles an error_page but not with {REMEDY_LOCATION}: {named}. "
            "A refused vendor download has to name a way in."
        )
        for handler in handlers:
            if handler.args[-1] == REMEDY_LOCATION:
                assert "403" in handler.args, f"{uri} does not route its 403 to {REMEDY_LOCATION}"

    # Looked up by NAME, not by `match_location`: nginx never routes a URI into
    # a named location, so asking the matcher for one would prove nothing.
    remedy = next(
        (loc for loc in server.locations if loc.args == (REMEDY_LOCATION,)),
        None,
    )
    assert remedy is not None, f"{REMEDY_LOCATION} is not defined"
    assert remedy.declared("internal"), f"{REMEDY_LOCATION} is reachable directly by a client"

    returns = remedy.effective("return")
    assert returns, f"{REMEDY_LOCATION} returns nothing"
    body = " ".join(returns[0].args)
    assert "403" in returns[0].args, f"{REMEDY_LOCATION} does not answer 403"
    assert "Sign in" in body, f"{REMEDY_LOCATION} names no remedy"
    assert f'href="{REAUTH_PATH}"' in body, (
        f"{REMEDY_LOCATION} does not link to {REAUTH_PATH}. Linking to `/` is a "
        "dead end for the one population that reaches this page: their token is "
        "still in localStorage, so `/` greets them as signed in."
    )

    # A refusal that echoes the request is a refusal that leaks. The reader
    # already knows what they clicked; anyone else must learn nothing.
    assert "$request_uri" not in body and "$uri" not in body
    for prefix in VENDOR_MEDIA_PREFIXES:
        assert prefix not in body, f"{REMEDY_LOCATION} echoes the {prefix} prefix"


@pytest.mark.unit
def test_both_servers_refuse_with_the_same_document():
    """ "Two servers, one rule" has to cover what the refused caller is TOLD.

    The refusal page is written twice — as a Python constant and as an nginx
    ``return 403`` string — and nothing but this compared them, so the two
    could drift into telling the same visitor different things. Whitespace
    between tags is the one difference allowed: the Python literal is wrapped
    for reading and the nginx one cannot be.
    """
    server = _tls_server()
    remedy = next(
        (loc for loc in server.locations if loc.args == (REMEDY_LOCATION,)),
        None,
    )
    assert remedy is not None

    served = " ".join(remedy.effective("return")[0].args[1:])
    assert _one_line(served) == _one_line(FORBIDDEN_REMEDY_HTML), (
        "nginx and config.protected_media refuse with different documents:\n"
        f"  nginx:  {_one_line(served)}\n"
        f"  django: {_one_line(FORBIDDEN_REMEDY_HTML)}"
    )


@pytest.mark.unit
def test_no_protected_prefix_is_left_publicly_cacheable():
    """An intermediary holding a copy is the gate failing a second time.

    ``expires`` and ``add_header`` are asked through :meth:`Block.effective`,
    which applies nginx's inheritance rule, so an ``expires 7d`` sitting in an
    enclosing block and no caching directive in the location itself fails here —
    the case a search for the literal string inside one block cannot see.
    """
    server = _tls_server()

    for prefix in VENDOR_MEDIA_PREFIXES:
        uri = f"/media/{prefix}zzqq-probe.pdf"
        location = server.match_location(uri)
        assert location is not None

        expires = location.effective("expires")
        assert expires, f"{uri} inherits no expires policy at all"
        assert (
            expires[0].value in NON_CACHING_EXPIRES
        ), f"{uri} is served with `expires {expires[0].value}` — a cacheable lifetime"

        cache_control = location.header("Cache-Control")
        assert cache_control is not None, f"{uri} sets no Cache-Control"
        assert (
            "public" not in cache_control.lower()
        ), f"{uri} is served Cache-Control: {cache_control}"


@pytest.mark.unit
def test_public_media_still_wins_its_own_match():
    """CONTROL: the parse proves a gate, not that every /media/ URI is gated.

    Without this, a model that matched everything to a protected block would
    pass the two tests above while describing a server that had closed the
    anonymous scan path.
    """
    server = _tls_server()
    qr = server.match_location("/media/inventory/qrcodes/item.png")
    assert qr is not None
    assert not qr.effective("auth_request"), "item QR codes are behind a login in nginx"


@pytest.mark.unit
def test_the_rendered_config_is_valid_nginx():
    """Hand the real consumer the real file, where the real consumer exists.

    Skipped rather than faked when nginx is not installed: the parse above is
    what CI always runs, and this is the stronger check when it can be had.
    ``nginx -t`` is a syntax and directive-context check — it proves the gate
    above is expressed in directives nginx accepts, not that the policy is
    right, which is what the parsed assertions are for.
    """
    binary = shutil.which("nginx")
    if binary is None:
        pytest.skip("nginx is not installed on this machine")

    rendered = render(NGINX_TEMPLATE.read_text(), TEMPLATE_VARIABLES)
    # `upstream` members are compose service names and nginx RESOLVES them at
    # config-test time, so outside the compose network it aborts on the first
    # one and validates nothing after it. The addresses are a deployment
    # detail, not what this template is being checked for; the location blocks
    # and their directives — which are — are untouched by this substitution.
    rendered = rendered.replace("server backend:8000;", "server 127.0.0.1:8000;")
    rendered = rendered.replace("server emqx:18083;", "server 127.0.0.1:18083;")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "conf.d").mkdir()
        (root / "conf.d" / "default.conf").write_text(rendered)
        (root / "nginx.conf").write_text(
            "events {}\n" f"http {{\n    include {root}/conf.d/*.conf;\n}}\n"
        )
        result = subprocess.run(  # nosec B603 — fixed binary, test-owned config
            [binary, "-t", "-c", str(root / "nginx.conf"), "-p", str(root)],
            capture_output=True,
            text=True,
        )

    # The TLS material is issued by certbot into the deployed container; a
    # developer box has no copy, and nginx opens it during `-t`. That is an
    # absent file rather than a rejected directive, so it is a skip, not a pass.
    if "cannot load certificate" in result.stderr or "BIO_new_file" in result.stderr:
        pytest.skip("nginx reached the TLS certificate paths, which this box has no copy of")
    assert result.returncode == 0, f"`nginx -t` rejected the rendered template:\n{result.stderr}"


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _mime_types(binary: str) -> Path | None:
    """The MIME map the installed nginx's own ``nginx.conf`` includes.

    Load-bearing for the test below rather than incidental: the bug it covers is
    that a type was resolved FROM the map at the gated location, so a harness
    that ran without one would answer ``text/html`` for every extension and
    could never see it. ``nginx/nginx.conf`` includes ``mime.types`` at ``http``
    level in production, and so does the config assembled here.
    """
    probe = subprocess.run(  # nosec B603 — fixed binary, no caller input
        [binary, "-V"], capture_output=True, text=True
    )
    match = re.search(r"--conf-path=(\S+)", probe.stderr)
    if match is None:
        return None
    candidate = Path(match.group(1)).parent / "mime.types"
    return candidate if candidate.is_file() else None


#: What a refused caller has to be handed, whatever they asked for.
REFUSED_DOCUMENTS = [
    "supplier_agreements/zzqq-standing-quote.pdf",
    "work_orders/receipts/2026/09/zzqq-receipt.jpg",
    "supplier_agreements/zzqq-scan-noextension",
]

SENTINEL = b"ZZQQ-REFUSED-DOCUMENT-BODY"


class _DenyingAuthStub(http.server.BaseHTTPRequestHandler):
    """Stands in for ``/api/auth/media-access/`` answering an anonymous caller.

    403 is what that endpoint really returns without a session — asserted by
    ``test_the_auth_request_endpoint_answers_the_way_nginx_needs`` below, which
    is what keeps this stub honest.
    """

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def refusing_nginx():
    """A real nginx serving THIS repo's template, with every caller refused.

    Only the deployment details a developer box cannot have are substituted —
    the listen port, the upstream addresses, the TLS material and the container
    media root. Every ``/media/`` location block, the ``@vendor_media_denied``
    named location and the ``http``-level MIME map are the file's own.
    """
    binary = shutil.which("nginx")
    if binary is None:
        pytest.skip("nginx is not installed on this machine")
    mime_types = _mime_types(binary)
    if mime_types is None:
        pytest.skip("the installed nginx ships no mime.types to include")

    stub = http.server.HTTPServer(("127.0.0.1", 0), _DenyingAuthStub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    port = _free_port()

    rendered = render(NGINX_TEMPLATE.read_text(), TEMPLATE_VARIABLES)
    rendered = rendered.replace("server backend:8000;", f"server 127.0.0.1:{stub.server_port};")
    rendered = rendered.replace("server emqx:18083;", f"server 127.0.0.1:{_free_port()};")
    rendered = rendered.replace("listen 443 ssl http2;", f"listen 127.0.0.1:{port};")
    rendered = rendered.replace("listen 80;", f"listen 127.0.0.1:{_free_port()};")
    # The certificates are issued into the deployed container and nginx OPENS
    # them at startup. Dropping the two paths is what lets this run at all; it
    # touches no location block and no header.
    rendered = "\n".join(line for line in rendered.splitlines() if "ssl_certificate" not in line)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        media = root / "media"
        for document in REFUSED_DOCUMENTS:
            path = media / document
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(SENTINEL)
        (root / "frontend").mkdir()
        rendered = rendered.replace("/app/media/", f"{media}/")
        rendered = rendered.replace("/app/frontend", str(root / "frontend"))

        (root / "conf.d").mkdir()
        (root / "conf.d" / "default.conf").write_text(rendered)
        (root / "nginx.conf").write_text(
            "events {}\n"
            "http {\n"
            f"    include {mime_types};\n"
            "    default_type application/octet-stream;\n"
            "    access_log off;\n"
            f"    include {root}/conf.d/*.conf;\n"
            "}\n"
        )

        server = subprocess.Popen(  # nosec B603 — fixed binary, test-owned config
            [binary, "-c", str(root / "nginx.conf"), "-p", str(root), "-g", "daemon off;"],
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise AssertionError(f"nginx would not start:\n{server.stderr.read()}")
                with contextlib.closing(socket.socket()) as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.05)
            else:
                raise AssertionError("nginx never accepted a connection")

            def get(uri: str):
                """``(status, content_type, body)`` for an unauthenticated GET."""
                request = urllib.request.Request(f"http://127.0.0.1:{port}{uri}")
                try:
                    with urllib.request.urlopen(request) as allowed:  # nosec B310
                        return (
                            allowed.status,
                            allowed.headers.get("Content-Type"),
                            allowed.read(),
                        )
                except urllib.error.HTTPError as refused:
                    return refused.code, refused.headers.get("Content-Type"), refused.read()

            yield get
        finally:
            server.terminate()
            server.wait(timeout=10)
            stub.shutdown()


def _media_type(content_type: str | None) -> str | None:
    """``text/html; charset=utf-8`` and ``text/html`` are the same answer."""
    return None if content_type is None else content_type.split(";")[0].strip().lower()


@pytest.mark.integration
@pytest.mark.parametrize("document", REFUSED_DOCUMENTS)
def test_both_servers_hand_a_refused_reader_a_page_they_can_read(
    document, refusing_nginx, db, tmp_path, settings
):
    """The remedy has to RENDER, and both servers have to render the same one.

    nginx resolves a content type from the URI extension at the gated location
    and the internal redirect to ``@vendor_media_denied`` carries it, so
    ``default_type text/html`` there did nothing: a refused supplier agreement
    went out ``403 application/pdf`` and a work-order receipt ``403 image/jpeg``.
    Chrome handed both to its PDF/image viewer — "Failed to load PDF document"
    instead of the remedy — and the inline script that records
    ``oms_pending_return_to`` never ran, so the way back to the document after
    signing in was dead too. Only an extension-less path got ``text/html``,
    which is why the extensions are the parameter here: one case would have
    missed it.

    Both halves are asserted together because "two servers, one rule" is this
    module's design and the two have now disagreed three times in the nginx
    direction. The gate itself is asserted alongside: the bytes must not come
    back either way — only what the reader is handed changes.
    """
    settings.MEDIA_ROOT = tmp_path
    served = tmp_path / document
    served.parent.mkdir(parents=True, exist_ok=True)
    served.write_bytes(SENTINEL)
    uri = f"/media/{document}"

    nginx_status, nginx_type, nginx_body = refusing_nginx(uri)
    django_response = APIClient().get(uri)
    django_body = (
        b"".join(django_response.streaming_content)
        if django_response.streaming
        else django_response.content
    )

    # The gate holds on both, first: a readable remedy over a leaked document
    # would be the worse bug.
    assert nginx_status == 403, f"nginx answered {nginx_status} for {uri}"
    assert django_response.status_code == 403, f"Django answered {django_response.status_code}"
    assert SENTINEL not in nginx_body and SENTINEL not in django_body

    assert _media_type(nginx_type) == "text/html", (
        f"nginx serves the refusal for {uri} as {nginx_type}, so a browser hands "
        "it to a viewer instead of rendering the remedy"
    )
    assert _media_type(django_response.headers.get("Content-Type")) == "text/html"
    assert _media_type(nginx_type) == _media_type(django_response.headers.get("Content-Type")), (
        "the two servers disagree about what a refused reader is handed:\n"
        f"  nginx:  {nginx_type}\n"
        f"  django: {django_response.headers.get('Content-Type')}"
    )

    for name, body in (("nginx", nginx_body), ("django", django_body)):
        text = body.decode()
        assert "Sign in required" in text, f"{name} refuses {uri} with no remedy"
        assert f'href="{REAUTH_PATH}"' in text, f"{name} refuses {uri} with no way in"
        assert "oms_pending_return_to" in text, (
            f"{name} refuses {uri} without recording where to return to"
        )


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
