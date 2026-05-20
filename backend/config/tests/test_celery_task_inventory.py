"""Pin docs/CELERY_TASKS.md to the actual Celery task registry (AC-28).

The inventory at ``docs/CELERY_TASKS.md`` is the operator-facing source of
truth for what background work exists, how it's triggered, and what its
recovery story looks like. The failure mode this test guards against is
silent drift: a new ``@shared_task`` is merged but the doc is not updated,
so an operator paging through the inventory misses a task whose retries
are double-invoicing donors or duplicating webhooks.

Companion to ``test_celery_schedule.py`` (beat schedule pinning).
"""

from pathlib import Path

from django.conf import settings

import pytest
from celery import current_app

DOCS_PATH = Path(settings.BASE_DIR).parent / "docs" / "CELERY_TASKS.md"

# Tasks not expected to appear in the inventory:
# - ``celery.*`` — Celery framework internals (backend_cleanup, chain, group, …).
# - ``imagekit.*`` — django-imagekit registers a private task
#   (``imagekit.cachefiles.backends._generate_file``) when its async backend
#   imports. OMS does not invoke it directly; surfacing it in the operator
#   inventory would be misleading. If we ever swap imagekit out, drop this
#   entry to surface any lingering callers.
INTERNAL_TASK_PREFIXES = ("celery.", "imagekit.")


def _registered_oms_tasks() -> set[str]:
    """Return registered task names owned by this project."""
    current_app.loader.import_default_modules()
    return {name for name in current_app.tasks if not name.startswith(INTERNAL_TASK_PREFIXES)}


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert DOCS_PATH.is_file(), (
        f"Expected Celery task inventory at {DOCS_PATH} but it is missing. "
        "Either the path changed or the doc was deleted — update this test "
        "and the references in deploy/COMPOSE_RUNBOOK.md §10."
    )
    return DOCS_PATH.read_text(encoding="utf-8")


@pytest.mark.unit
class TestCeleryTaskInventoryDoc:
    """Every registered task must appear in ``docs/CELERY_TASKS.md``."""

    def test_inventory_doc_exists(self, doc_text):
        # Cheap smoke: the doc shouldn't be empty or have lost its header.
        assert "Celery task inventory" in doc_text

    def test_every_registered_task_is_documented(self, doc_text):
        registered = _registered_oms_tasks()
        # Match the way the inventory cites tasks: inside backticks, e.g.
        # ``| `donations.tasks.send_quarterly_donor_updates` | ...``. Looking
        # for the backtick form (not bare substring) keeps the assertion
        # honest — a stray narrative mention isn't a real inventory entry.
        missing = sorted(name for name in registered if f"`{name}`" not in doc_text)
        assert not missing, (
            "These registered Celery tasks are not documented in "
            f"docs/CELERY_TASKS.md: {missing}. Add a row to the matching "
            "app section (Trigger / Side effects / Retries / Timeout / "
            "Idempotency / Recovery) in the same PR that introduces the task."
        )
