"""One pending anonymous reorder request per item.

The captain's decision: "One pending anon request per item." The scan page
retries a failed submit a bounded number of times, so a POST whose RESPONSE was
lost — a phone dropping the connection, a proxy answering 502 after the server
already committed — filed the same need twice and purchasing saw two rows for
one item. The client-side re-read in ``ScanPage.tsx`` narrows that window; this
closes it, for every caller of the public endpoint including ScanTTY.

Everything here goes through the ROUTED endpoint, twice. A test that called
``ReorderRequestCreateSerializer`` directly would prove the rule exists without
proving the URL a scanner actually hits obeys it, or that the member is told
the right thing when it does.
"""

import json
import threading
import time
from unittest import mock

from django.db import connection
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.tests.factories import InventoryItemFactory
from reorder_queue import serializers
from reorder_queue.models import ReorderRequest
from reorder_queue.tests.factories import ReorderRequestFactory

pytestmark = pytest.mark.django_db


SCAN_PAYLOAD = {
    "quantity": 4,
    "requested_by": "Anonymous",
    "request_notes": "Auto-submitted via QR scan",
}


def scan(client, item, **overrides):
    """POST the public create endpoint exactly as an anonymous scan does."""
    payload = {"item": str(item.id), **SCAN_PAYLOAD, **overrides}
    return client.post(reverse("reorderrequest-list"), payload, format="json")


@pytest.mark.integration
class TestAnonymousDuplicateRequests:
    """The rule, and the three outcomes a scanner can be shown."""

    def test_first_anonymous_scan_files_a_request(self, api_client):
        """Outcome one: FILED. 201, a new row, and ``already_requested`` false.

        The flag is on the created response too, not only the duplicate one, so
        a client reads one field rather than having to notice 201 vs 200 — a
        distinction anything checking ``response.ok`` flattens.
        """
        item = InventoryItemFactory()

        response = scan(api_client, item)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["already_requested"] is False
        assert ReorderRequest.objects.filter(item=item).count() == 1

    def test_second_anonymous_scan_files_nothing(self, api_client):
        """THE rule: while one is pending, a second anonymous scan adds no row."""
        item = InventoryItemFactory()

        first = scan(api_client, item)
        second = scan(api_client, item)

        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_200_OK
        assert ReorderRequest.objects.filter(item=item).count() == 1

    def test_the_duplicate_is_reported_as_already_recorded(self, api_client):
        """Outcome two: ALREADY RECORDED — never a failure, never silent.

        What must not happen is the scanner being told nothing was filed. The
        response is a success carrying the pending request, a flag that says
        which outcome this is, and wording that says the need is on file.
        """
        item = InventoryItemFactory(name="Blue nitrile gloves (M)")

        first = scan(api_client, item)
        second = scan(api_client, item)

        assert second.status_code == status.HTTP_200_OK
        assert second.data["already_requested"] is True
        # It points at the request that IS recorded, not at a phantom row.
        assert second.data["id"] == first.data["id"]
        assert second.data["status"] == ReorderRequest.Status.PENDING
        detail = second.data["detail"]
        assert "Blue nitrile gloves (M)" in detail
        assert "already recorded" in detail

    def test_a_genuine_failure_is_still_a_failure(self, api_client):
        """Outcome three: COULD NOT TELL. A real error keeps the error
        envelope and never acquires ``already_requested``, so a client cannot
        read "we could not file this" as "this was already filed"."""
        item = InventoryItemFactory()
        scan(api_client, item)

        response = api_client.post(
            reverse("reorderrequest-list"),
            {"item": "00000000-0000-0000-0000-000000000000", **SCAN_PAYLOAD},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("already_requested") is not True

    def test_anonymous_submission_is_not_narrowed(self, api_client):
        """This decision constrains DUPLICATES, not access. An anonymous
        caller is never refused and never asked to log in — not on the first
        scan and not on the duplicate."""
        item = InventoryItemFactory()

        first = scan(api_client, item)
        second = scan(api_client, item)

        assert first.status_code != status.HTTP_401_UNAUTHORIZED
        assert first.status_code != status.HTTP_403_FORBIDDEN
        assert second.status_code != status.HTTP_401_UNAUTHORIZED
        assert second.status_code != status.HTTP_403_FORBIDDEN

    def test_the_duplicate_response_leaks_no_admin_metadata(self, api_client):
        """A second scan must not expose who filed the blocking request or
        their free-text notes."""
        item = InventoryItemFactory()
        scan(api_client, item)

        second = scan(api_client, item)

        assert set(second.data) == {
            "id",
            "item",
            "quantity",
            "priority",
            "status",
            "already_requested",
            "detail",
        }
        assert "requested_by" not in second.data
        assert "request_notes" not in second.data

    def test_the_duplicate_never_exposes_a_named_requester(self, api_client):
        """The case the narrowed shape exists for: the blocking row was filed
        by somebody else, with their name and their notes on it.

        Any pending row blocks, so an unauthenticated caller who knows an item
        id reaches this response — and must not be able to read who else asked
        for the item or what they wrote. Asserted on the VALUES, not only the
        key set: a future field carrying the same text would slip past a key
        check.
        """
        item = InventoryItemFactory(name="Shop rags")
        ReorderRequestFactory(
            item=item,
            status=ReorderRequest.Status.PENDING,
            requested_by="Dana Okafor (shop lead)",
            request_notes="for the Tuesday powdercoat job, do not substitute",
        )

        response = scan(api_client, item)

        assert response.status_code == status.HTTP_200_OK
        body = json.dumps(response.data, default=str)
        assert "Dana Okafor" not in body
        assert "powdercoat" not in body

    def test_a_pending_request_for_another_item_does_not_block(self, api_client):
        """Per ITEM. A pending request for a different item is a different
        need and blocks nothing."""
        blocked, other = InventoryItemFactory(), InventoryItemFactory()
        ReorderRequestFactory(item=other, status=ReorderRequest.Status.PENDING)

        response = scan(api_client, blocked)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["already_requested"] is False

    def test_no_second_admin_notification_for_a_duplicate(self, api_client):
        """Nothing new arrived in the queue, so nothing new is announced. A
        second "New Reorder Request" for a row admins already have is the very
        duplicate this endpoint just declined to file."""
        item = InventoryItemFactory()

        with mock.patch("notifications.services.notify_admins") as notify_admins:
            scan(api_client, item)
            assert notify_admins.call_count == 1

            scan(api_client, item)
            assert notify_admins.call_count == 1


@pytest.mark.integration
class TestWhichStatesClearTheBlock:
    """Only ``pending`` blocks. Every other state in the repository's state
    machine clears it, and each for its own reason — see
    ``DUPLICATE_BLOCKING_STATUSES`` in ``reorder_queue/serializers.py``."""

    @pytest.mark.parametrize(
        "cleared_status",
        [
            # A human has acted on it; purchasing owns it now, so a fresh scan
            # is new signal rather than the same submission arriving twice.
            ReorderRequest.Status.APPROVED,
            ReorderRequest.Status.ORDERED,
            # Fulfilled, and refused: neither may leave the item permanently
            # unrequestable, which suppressing on them would amount to.
            ReorderRequest.Status.RECEIVED,
            ReorderRequest.Status.CANCELLED,
        ],
    )
    def test_a_non_pending_request_does_not_block(self, api_client, cleared_status):
        item = InventoryItemFactory()
        ReorderRequestFactory(item=item, status=cleared_status)

        response = scan(api_client, item)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["already_requested"] is False
        assert ReorderRequest.objects.filter(item=item, status="pending").count() == 1

    def test_a_pending_request_blocks_whoever_filed_it(self, api_client):
        """The row records no creator identity — ``requested_by`` is free text
        the client supplies — so the rule reads the STATE, not who typed it.
        The member is still told the truth: a request for this item is pending
        and their need is recorded."""
        item = InventoryItemFactory()
        ReorderRequestFactory(
            item=item, status=ReorderRequest.Status.PENDING, requested_by="Dana (staff)"
        )

        response = scan(api_client, item)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["already_requested"] is True
        assert ReorderRequest.objects.filter(item=item).count() == 1


@pytest.mark.integration
class TestAuthenticatedCreateIsUnchanged:
    """The rule covers anonymous submissions, which is what the decision
    covers. A logged-in member has the queue, the request list and a name on
    the row, so a second request from them is a deliberate act rather than a
    retried POST."""

    def test_an_authenticated_member_may_still_file_against_a_pending_request(
        self, authenticated_client
    ):
        client, _user = authenticated_client
        item = InventoryItemFactory()
        ReorderRequestFactory(item=item, status=ReorderRequest.Status.PENDING)

        response = client.post(
            reverse("reorderrequest-list"),
            {"item": str(item.id), "quantity": 4},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["already_requested"] is False
        assert ReorderRequest.objects.filter(item=item).count() == 2


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestConcurrentScans:
    """Two scans arriving at once still produce one row.

    What guarantees it is a row-level lock on the ITEM, taken inside the same
    transaction as the check and the insert. Locking the reorder rows could not
    work: in the race being closed there is no pending row yet to lock. See
    ``ReorderRequestCreateSerializer.create`` for the races that remain
    accepted — an anonymous scan racing an AUTHENTICATED create, which takes no
    lock by design.
    """

    def test_two_simultaneous_scans_file_one_request(self):
        """The real race: two scans for the same item, neither of which can see
        a pending request when it looks.

        The window is held open deliberately — the duplicate check is made to
        dawdle half a second before returning — so this is not a coin toss.
        Drop the item lock from ``ReorderRequestCreateSerializer.create`` and
        both threads read "nothing pending" and both insert, which is exactly
        the outcome purchasing saw. Note it is the CHECK the lock has to cover:
        inserting takes only a ``FOR KEY SHARE`` lock on the item through the
        foreign key, and two of those do not conflict.
        """
        if not connection.features.has_select_for_update:
            pytest.skip("requires row-level select_for_update locking")

        item = InventoryItemFactory()
        real_lookup = serializers.pending_request_for

        def dawdling_lookup(looked_up):
            found = real_lookup(looked_up)
            time.sleep(0.5)
            return found

        at_the_line = threading.Barrier(2, timeout=30)
        responses = []
        lock = threading.Lock()

        def scan_anonymously():
            try:
                at_the_line.wait()
                response = APIClient().post(
                    reverse("reorderrequest-list"),
                    {"item": str(item.id), **SCAN_PAYLOAD},
                    format="json",
                )
                with lock:
                    responses.append(response)
            finally:
                connection.close()

        with mock.patch.object(serializers, "pending_request_for", dawdling_lookup):
            scans = [threading.Thread(target=scan_anonymously, daemon=True) for _ in range(2)]
            for thread in scans:
                thread.start()
            for thread in scans:
                thread.join(timeout=30)

        assert [thread.is_alive() for thread in scans] == [False, False]
        assert ReorderRequest.objects.filter(item=item).count() == 1
        # One filed it, the other was told it was already recorded. Neither was
        # told nothing happened.
        assert sorted(response.status_code for response in responses) == [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ]
