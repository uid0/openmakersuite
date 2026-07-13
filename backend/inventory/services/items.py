"""Write-path services for :class:`inventory.models.InventoryItem`.

These hold the workflow side effects that used to live inline in
``InventoryItem.save()`` so the override stays a thin, documented delegator
(gh #887). ``save()`` still calls them, so the many bare
``InventoryItem.objects.create(...)`` callers keep generating a SKU and
scheduling the image download exactly as before. The one deliberate change is
that the async download now fires on ``transaction.on_commit`` instead of
mid-transaction (see :func:`schedule_image_download`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from inventory.models.core import InventoryItem


def assign_sku(item: "InventoryItem") -> None:
    """Assign a generated SKU when the item does not already have one.

    A tiny local invariant kept synchronous on the create path because the SKU
    is returned in the create response; only the assignment is factored out of
    ``save()`` so the behaviour has a named, testable home. No-op when a SKU is
    already set, so it never regenerates an existing value.
    """
    if not item.sku:
        from inventory.models.core import generate_sku

        item.sku = generate_sku()


def should_download_image(item: "InventoryItem") -> bool:
    """Return ``True`` when the item names a source image URL but has no file yet."""
    return bool(item.image_url and not item.image)


def schedule_image_download(item: "InventoryItem") -> None:
    """Enqueue the async image download once the current transaction commits.

    The enqueue is wrapped in ``transaction.on_commit`` so the Celery worker
    never races ahead of the row it reads. Previously ``save()`` called
    ``.delay()`` inline, which could dispatch the task before the item row was
    committed — or for a row a later rollback discarded — leaving the worker to
    fetch against a missing/uncommitted item. Callers decide *whether* to
    download via :func:`should_download_image`; this schedules the *how*.
    """
    from inventory.tasks import download_image_from_url

    item_id = str(item.id)
    image_url = item.image_url
    transaction.on_commit(lambda: download_image_from_url.delay(item_id, image_url))
