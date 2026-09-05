"""How much of the API surface the anonymous crawl actually reached.

"Never conflate 'found nothing' with 'could not tell'." The crawl in
``test_anonymous_vendor_exposure`` proves things about the URLs it can turn into
a request. This module makes the REST of the surface visible and bounded, so a
green crawl is a measured claim rather than an unmeasured one.

Two kinds of route the crawl does not exercise, and what is true of each:

* **no-GET** — routes whose only methods are POST/PUT/PATCH/DELETE. A crawl that
  issued those would be writing to the database, so it does not. Anonymous WRITE
  exposure on the purchasing surface has its own dedicated gate,
  ``reorder_queue/tests/test_receiving_action_permissions.py``, which derives
  every routed action and refuses to pass if one answers an unauthenticated
  caller. The vendor-bearing write paths are covered there; the two anonymous
  writes this branch must NOT break (scan-to-reorder, issue reporting) are
  covered in the crawl's own module.
* **unfillable** — patterns this probe cannot build a concrete path for (a regex
  with alternation, a converter it has no value for). Those are listed by the
  assertion below, and the vendor-bearing ones are covered by hand with real
  primary keys in ``test_vendor_only_endpoints_require_authentication`` and
  ``test_public_surfaces_stay_reachable_but_drop_vendor_fields``.

The number that matters is the third one: how much of the surface a request
actually reached. It is asserted with a floor rather than printed, so a change
that quietly makes most routes unfillable — which would turn the crawl green by
reaching nothing — fails here.
"""

from __future__ import annotations

import collections

import pytest
from rest_framework.test import APIClient

from config.tests.vendor_exposure_probe import (
    crawl_anonymously,
    routed_get_urls,
    seed_vendor_fixture,
)


@pytest.mark.integration
@pytest.mark.django_db
def test_the_crawl_reaches_most_of_the_get_surface_and_names_what_it_cannot():
    objs = seed_vendor_fixture()
    fill = {
        "__uuid__": str(objs["item"].id),
        "__default_pk__": str(objs["supplier"].id),
        "item_id": str(objs["item"].id),
        "location_id": str(objs["location"].id),
        "supplier_id": str(objs["supplier"].id),
    }

    reachable, unreachable = routed_get_urls(fill)
    kinds = collections.Counter(kind for kind, *_ in unreachable)

    # The crawl builds a request for the large majority of GET-serving routes.
    # A regression that dropped this would make a green crawl meaningless.
    assert len(reachable) >= 400, (
        f"the crawl only built {len(reachable)} requests; it used to build 470+. "
        "A green run proves correspondingly less — fix the path builder rather "
        "than lowering this floor."
    )
    # Nothing unfillable may be a route the vendor endpoints live on: those are
    # covered by hand, and this asserts the hand-list is still the right one.
    vendor_prefixes = (
        "api/inventory/^suppliers",
        "api/inventory/^item-suppliers",
        "api/inventory/^price-history",
        "api/inventory/^supplier-agreements",
        "api/reorders/^purchase-orders",
    )
    unfillable_vendor_routes = [
        route
        for kind, route, _view in unreachable
        if kind == "unfillable" and route.startswith(vendor_prefixes)
    ]
    assert (
        unfillable_vendor_routes == []
    ), "these vendor routes are neither crawled nor covered by hand:\n" + "\n".join(
        unfillable_vendor_routes
    )
    assert kinds["no-GET"] > 0, "the no-GET bucket is empty; the split stopped working"


@pytest.mark.integration
@pytest.mark.django_db
def test_the_crawl_actually_gets_answers_rather_than_errors():
    """A surface answering 500 everywhere would also disclose nothing.

    So this pins that most probed routes return a real HTTP status and that a
    substantial number answer 200 — the crawl is reading live payloads, not
    counting stack traces.
    """
    objs = seed_vendor_fixture()
    fill = {
        "__uuid__": str(objs["item"].id),
        "__default_pk__": str(objs["supplier"].id),
        "item_id": str(objs["item"].id),
        "location_id": str(objs["location"].id),
        "supplier_id": str(objs["supplier"].id),
    }

    _disclosures, transcript, _unreachable = crawl_anonymously(APIClient(), fill)
    statuses = collections.Counter(entry[3] for entry in transcript)

    assert statuses[200] >= 50, f"only {statuses[200]} routes answered 200: {statuses}"

    # THE ONE KNOWN EXCEPTION CLASS, named rather than tolerated silently.
    #
    # A `@action` method whose signature omits `format=None` raises
    # ``TypeError: ...() got an unexpected keyword argument 'format'`` when
    # reached through DRF's format-suffix route (``items/low_stock.json``).
    # PRE-EXISTING and unrelated to vendor exposure — it predates this branch,
    # it is a 500 rather than a disclosure, and every one of these routes has a
    # suffix-less twin that this crawl DOES fetch and search, so nothing goes
    # uncovered by it. Reported, not fixed: it is outside this branch's scope
    # cap, and a blanket signature change across dozens of actions is its own
    # piece of work.
    #
    # Asserted as an exact set rather than ignored, so a NEW failure mode — one
    # that might be an exception thrown while serialising vendor data — still
    # fails here instead of being absorbed by a permissive allowance.
    exceptions = {status for status in statuses if isinstance(status, str)}
    assert exceptions <= {
        "EXC:TypeError"
    }, f"a new exception class appeared while crawling: {exceptions - {'EXC:TypeError'}}"
    erroring = [entry[0] for entry in transcript if entry[3] == "EXC:TypeError"]
    assert all(path.endswith(".json") for path in erroring), (
        "the format-suffix TypeError has spread to a route without a suffix, which "
        f"is a different bug: {[p for p in erroring if not p.endswith('.json')]}"
    )
