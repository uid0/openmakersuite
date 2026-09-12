"""The router every app's ``urls.py`` registers its viewsets on.

``ApiRouter`` is ``DefaultRouter`` with ``include_format_suffixes`` turned OFF,
because the format-suffix routes that flag generates were never a working part
of this API.

WHY THEY ARE GONE RATHER THAN REPAIRED
--------------------------------------
``DefaultRouter`` registers a second, ``.<format>``-suffixed path for every
route, and passes the captured suffix to the handler as a ``format`` keyword
argument. DRF's own generic handlers accept it (``def list(self, request, *args,
**kwargs)``); a hand-written ``@action`` declared ``def low_stock(self,
request):`` does not, so ``GET /api/inventory/items/low_stock.json`` raised
``TypeError: ... got an unexpected keyword argument 'format'`` — a 500 — instead
of answering.

276 of the 828 routed handlers were in that shape: a third of the suffix surface
had never once returned a response. That is the evidence the surface has no
callers, and a survey of the ones there are agrees:

* Nothing in this repository builds a suffixed URL — not the React app, not the
  backend tests, not the Playwright e2e fixtures, not the docs. The only literal
  ``.json`` paths here (``forgekey`` ``desired.json``, ``.well-known/jwks.json``)
  are hand-written ``path()`` entries, not router suffixes, and are untouched by
  this flag.
* ScanTTY, the cross-project consumer, builds every path trailing-slash and
  never appends a suffix (verified against ``uid0/scantty`` ``main`` at
  ``498af69448333a23264b03166029786e1968978b``).
* The published OpenAPI schema contains **zero** of them: drf-spectacular
  filters format-suffix routes out, so all 584 documented paths are suffix-less.
  Nothing generated from our own schema could call one.
* ``config.permission_matrix`` already treats the suffixed path as a duplicate
  of its twin and keeps only the first, on the grounds that the permissions are
  identical — they resolve to the same view.

So repairing them would have meant adding ``format=None`` to 276 signatures
across 16 apps, and taxing every future ``@action`` with the same requirement,
to keep alive a duplicate URL for every endpoint that no caller, and not even
our own schema, has ever used. Dropping the routes answers the same requests
with a plain 404 — a refusal, not a raise — and leaves one way to ask for JSON:
content negotiation via ``Accept: application/json``, which is what the React
app and ScanTTY already send.

This does NOT affect the ``?format=csv`` / ``?format=pdf`` QUERY parameters that
the export endpoints read; those are read from ``request.query_params`` and have
nothing to do with suffix routing.

``config.tests.test_format_suffix_routes`` holds the line: it fails if any
router in the URLconf starts emitting format-suffix routes again.
"""

from rest_framework.routers import DefaultRouter

__all__ = ["ApiRouter"]


class ApiRouter(DefaultRouter):
    """``DefaultRouter`` without the ``.<format>`` suffix routes."""

    include_format_suffixes = False
