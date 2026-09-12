"""Every test gets its own empty ``MEDIA_ROOT`` outside the working tree.

Pins the root ``conftest.isolated_media_root`` fixture. Without it these tests
write into ``backend/media``, and ``test_same_name_saved_again_keeps_its_name``
fails even within one run: the earlier case's file is still there, so Django
stores the new one as ``receipt_<random>.pdf``. Across runs the same leftover
breaks every test that asserts a stored filename.

``TestClassLevelSetupIsIsolatedToo`` pins the session layer underneath: class
setup runs before any function-scoped fixture, and without that layer
``inventory/tests/test_serialized_component_admin.py``'s ``setUpTestData``
wrote an item image into ``backend/media`` on every run.
"""

from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, TestCase

import pytest

from reorder_queue.models import PurchaseOrderAttachment

PROBE_NAME = "media-isolation-probe/receipt.pdf"


def _working_tree():
    return Path(settings.BASE_DIR).resolve().parent


def _assert_isolated():
    media_root = Path(settings.MEDIA_ROOT).resolve()
    assert not media_root.is_relative_to(_working_tree())
    assert list(media_root.iterdir()) == []
    assert Path(default_storage.path(PROBE_NAME)).resolve().is_relative_to(media_root)


def test_media_root_is_empty_and_outside_the_working_tree():
    _assert_isolated()


def test_file_field_storage_follows_the_isolated_media_root():
    storage = PurchaseOrderAttachment._meta.get_field("file").storage
    assert (
        Path(storage.path(PROBE_NAME)).resolve().is_relative_to(Path(settings.MEDIA_ROOT).resolve())
    )


@pytest.mark.parametrize("attempt", [1, 2])
def test_same_name_saved_again_keeps_its_name(attempt):
    """Two cases save the same name; without isolation the second is renamed."""
    assert default_storage.save(PROBE_NAME, ContentFile(b"%PDF")) == PROBE_NAME


def test_media_root_leaves_the_tests_own_tmp_path_alone(isolated_media_root, tmp_path):
    assert Path(settings.MEDIA_ROOT) == isolated_media_root
    assert not isolated_media_root.is_relative_to(tmp_path)


class TestUnittestStyleCasesAreIsolatedToo(SimpleTestCase):
    def test_media_root_is_empty_and_outside_the_working_tree(self):
        _assert_isolated()
        assert default_storage.save(PROBE_NAME, ContentFile(b"%PDF")) == PROBE_NAME


class TestClassLevelSetupIsIsolatedToo(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.saved_path = Path(
            default_storage.path(default_storage.save(PROBE_NAME, ContentFile(b"%PDF")))
        )

    def test_class_level_save_lands_outside_the_working_tree(self):
        assert not self.saved_path.resolve().is_relative_to(_working_tree())
