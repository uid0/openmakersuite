"""Tests for :mod:`inventory.services.items` — SKU assignment and image-download
scheduling extracted from ``InventoryItem.save()`` (gh #887).

The image download is the one behavioural change: it now fires on
``transaction.on_commit`` instead of inline, so the Celery worker can never
race ahead of the committed row.
"""

from unittest.mock import patch

import pytest

from inventory.services.items import (
    assign_sku,
    schedule_image_download,
    should_download_image,
)
from inventory.tests.factories import InventoryItemFactory


class TestAssignSku:
    """assign_sku — the tiny SKU invariant, now a named service function."""

    def test_assigns_when_missing(self):
        item = InventoryItemFactory.build(sku="")
        assign_sku(item)
        assert item.sku

    def test_preserves_existing(self):
        item = InventoryItemFactory.build(sku="KEEP-ME")
        assign_sku(item)
        assert item.sku == "KEEP-ME"

    @pytest.mark.django_db
    def test_save_generates_sku_when_blank(self):
        item = InventoryItemFactory(sku="", image=None)
        assert item.sku  # save() delegated to assign_sku and filled it in


class TestShouldDownloadImage:
    """should_download_image — the download decision, isolated and testable."""

    def test_true_when_url_without_image(self):
        item = InventoryItemFactory.build(image_url="https://example.com/x.jpg", image=None)
        assert should_download_image(item) is True

    def test_false_without_url(self):
        item = InventoryItemFactory.build(image_url="", image=None)
        assert should_download_image(item) is False

    def test_false_when_image_already_present(self):
        item = InventoryItemFactory.build(
            image_url="https://example.com/x.jpg", image="existing.jpg"
        )
        assert should_download_image(item) is False


@pytest.mark.django_db
class TestScheduleImageDownloadOnCommit:
    """The behavioural fix (AC-4): the download enqueues on commit, not before."""

    def test_save_enqueues_on_commit_not_before(self, django_capture_on_commit_callbacks):
        # Create with no pending image so the factory's own saves schedule nothing,
        # then drive a single save() with an image to download — one clean enqueue.
        item = InventoryItemFactory(image=None, image_url="")
        item.image_url = "https://example.com/x.jpg"

        with patch("inventory.tasks.download_image_from_url.delay") as mock_delay:
            with django_capture_on_commit_callbacks(execute=True):
                item.save()
                # Still inside the transaction: the task must NOT have dispatched.
                mock_delay.assert_not_called()
            # The transaction committed: the on_commit callback fired.
            mock_delay.assert_called_once_with(str(item.id), "https://example.com/x.jpg")

    def test_no_enqueue_without_image_url(self, django_capture_on_commit_callbacks):
        with patch("inventory.tasks.download_image_from_url.delay") as mock_delay:
            with django_capture_on_commit_callbacks(execute=True):
                InventoryItemFactory(image_url="", image=None)
            mock_delay.assert_not_called()

    def test_no_enqueue_when_image_already_present(self, django_capture_on_commit_callbacks):
        with patch("inventory.tasks.download_image_from_url.delay") as mock_delay:
            with django_capture_on_commit_callbacks(execute=True):
                # Factory supplies a generated image, so there is nothing to fetch.
                InventoryItemFactory(image_url="https://example.com/x.jpg")
            mock_delay.assert_not_called()

    def test_schedule_service_defers_until_commit(self, django_capture_on_commit_callbacks):
        item = InventoryItemFactory(image_url="https://example.com/y.jpg", image=None)
        with patch("inventory.tasks.download_image_from_url.delay") as mock_delay:
            with django_capture_on_commit_callbacks(execute=True):
                schedule_image_download(item)
                mock_delay.assert_not_called()
            mock_delay.assert_called_once_with(str(item.id), "https://example.com/y.jpg")
