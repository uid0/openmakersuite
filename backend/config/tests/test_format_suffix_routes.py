"""DRF format-suffix routes: a suffixed URL must never raise.

``DefaultRouter`` registered a second ``.<format>`` path for every route and
passed the captured suffix to the handler as a ``format`` keyword argument. An
``@action`` written ``def low_stock(self, request):`` cannot accept it, so the
suffixed URL answered ``TypeError`` — a 500 — rather than a response. 276 of the
828 routed handlers were in that shape.

``config.routers.ApiRouter`` stops generating those routes; the reasoning, and
the evidence that nothing called them, is in that module's docstring. These
tests pin the outcome from two directions: the concrete URLs answer instead of
raising, and no router may start emitting suffix routes again.
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver
from rest_framework.test import APIClient

# Real routed URLs, each confirmed to raise ``TypeError`` before this change.
# Four apps and both router kinds, so this is not a single viewset's quirk.
# These are requested through the URLconf with the test client: the assertion is
# about what Django's own resolution and dispatch do, not about a reimplementation
# of either.
SUFFIXED_ACTION_URLS = [
    "/api/inventory/items/low_stock.json",
    "/api/reorders/requests/pending.json",
    "/api/reorders/reports/purchasing/lead_time_analysis.json",
    "/api/reorders/purchase-orders/reorder_data.json",
    "/api/reorders/webhooks/test-status.json",
    "/api/screens/screens/status.json",
]


@pytest.mark.django_db
@pytest.mark.parametrize("url", SUFFIXED_ACTION_URLS)
def test_format_suffix_on_a_routed_action_does_not_raise(url):
    """The suffixed URL answers. It used to raise ``TypeError``.

    Requested as a superuser deliberately: an anonymous request to most of these
    is refused in ``initial()``, BEFORE the handler runs, so it would pass
    without the handler ever being reached and prove nothing. Only a caller who
    is allowed through gets far enough to hit the signature.

    The assertion is that the request COMPLETES. 404 is the expected answer now
    that the suffix is not routed, and it is a refusal the caller can read; what
    is being excluded is the unhandled exception. ``raise_request_exception``
    defaults to True, so an exception in the view propagates here and fails the
    test rather than being flattened into a 500.
    """
    user = get_user_model().objects.create_superuser(
        username="fmt-suffix-probe", email="fmt-suffix-probe@example.com", password="x"
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.get(url)

    assert response.status_code == 404, (
        f"{url} answered {response.status_code}; the format suffix is no longer routed, "
        "so a plain 404 is the contract."
    )


def _url_patterns(resolver, prefix=""):
    for entry in resolver.url_patterns:
        pattern = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            yield from _url_patterns(entry, pattern)
        elif isinstance(entry, URLPattern):
            yield pattern


def test_no_router_emits_format_suffix_routes():
    """No route may capture a ``format`` group.

    Derived from the real URLconf rather than from a list, so a viewset
    registered on a plain ``DefaultRouter`` in a new app fails here instead of
    quietly reintroducing 500s. Before ``ApiRouter`` this found 486 patterns.

    A ``format`` GROUP is the router's suffix mechanism and the only thing that
    reaches a handler as a keyword argument. Hand-written paths that merely END
    in ``.json`` (``forgekey/epaper/<id>/desired.json``, ``.well-known/jwks.json``)
    capture nothing and are deliberately NOT in scope: they route to views that
    expect them.
    """
    offenders = [p for p in _url_patterns(get_resolver()) if re.search(r"\(\?P<format>", p)]

    assert offenders == [], (
        f"{len(offenders)} URL pattern(s) capture a `format` group, so a suffixed request "
        "passes `format=` to a handler that may not accept it. Register the viewset on "
        f"`config.routers.ApiRouter`, not `DefaultRouter`. First few: {offenders[:5]}"
    )
