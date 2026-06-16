"""Guard the celery -> tzlocal dependency pin (oms-zkr3o).

celery imports ``tzlocal`` at module load — ``celery.utils.time`` does
``from tzlocal import get_localzone`` — and declares the dependency *unpinned*
in its own metadata. That left pip free to resolve whatever the latest
``tzlocal`` release happened to be at install time.

On 2026-06-16 that latest was ``tzlocal==5.4.2``: a broken release whose wheel
ships only ``.dist-info`` metadata and no importable module code. pip
"successfully installs" it, but ``import tzlocal`` (and therefore
``import celery``, which OMS imports at Django startup via ``config/__init__``)
then raises ``ModuleNotFoundError`` under the CI Python 3.14 toolchain. That
took down every Backend Tests / Docker Build / E2E / Prod-Smoke job at once and
wedged the merge queue.

The fix pins ``tzlocal`` to a known-good release directly in
``requirements.txt``. These tests fail CI — instead of letting the queue break
silently again — if that pin is dropped, loosened off an exact ``==``, or moved
to the known-broken release, and if celery's tzlocal-backed time helpers ever
stop importing.

Companion to the other ``config/tests/test_celery_*.py`` drift guards.
"""

import re
from pathlib import Path

from django.conf import settings

import pytest

REQUIREMENTS = Path(settings.BASE_DIR) / "requirements.txt"

# tzlocal 5.4.2 was published as a metadata-only wheel with no importable
# module code; pinning back to it would reintroduce oms-zkr3o.
KNOWN_BROKEN_TZLOCAL = "5.4.2"


def _tzlocal_requirement() -> str:
    """Return the ``tzlocal`` requirement line from requirements.txt.

    Strips inline comments and surrounding whitespace. Returns ``""`` if no
    direct ``tzlocal`` requirement is present.
    """
    for raw in REQUIREMENTS.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if re.match(r"^tzlocal\b", line, re.IGNORECASE):
            return line
    return ""


@pytest.mark.unit
class TestTzlocalPin:
    """The pin must exist, be exact, and not point at the broken release."""

    def test_tzlocal_is_pinned_exactly(self):
        spec = _tzlocal_requirement()
        assert spec, (
            "tzlocal must be pinned directly in requirements.txt. celery "
            "declares it unpinned, so without our own pin pip is free to "
            "resolve a broken release and break every backend job (oms-zkr3o)."
        )
        assert "==" in spec, (
            f"tzlocal must use an exact '==' pin to keep a broken upstream "
            f"release from being resolved; got: {spec!r}"
        )

    def test_tzlocal_not_pinned_to_known_broken_release(self):
        spec = _tzlocal_requirement()
        version = spec.split("==", 1)[1].strip() if "==" in spec else ""
        assert version != KNOWN_BROKEN_TZLOCAL, (
            f"tzlocal=={KNOWN_BROKEN_TZLOCAL} is the empty-wheel release that "
            "caused oms-zkr3o (installs but is not importable); pin to a "
            "known-good version."
        )


@pytest.mark.unit
class TestTzlocalImportChain:
    """The exact import path that broke must work end to end."""

    def test_tzlocal_get_localzone_importable(self):
        # This is the precise import celery.utils.time performs. A
        # metadata-only wheel makes it raise ModuleNotFoundError.
        from tzlocal import get_localzone

        tz = get_localzone()
        assert tz is not None
        # get_localzone() returns a tzinfo (zoneinfo.ZoneInfo on py3.9+).
        assert hasattr(tz, "utcoffset")

    def test_celery_time_utils_import(self):
        # celery.utils.time is the module that pulls tzlocal during
        # ``import celery``; importing it exercises the whole chain.
        from celery.utils.time import maybe_make_aware

        assert callable(maybe_make_aware)
