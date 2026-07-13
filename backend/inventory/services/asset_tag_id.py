"""Asset tag identifier generation, checksum, and validation.

Asset tags use the human-meaningful format ``DMS-YYANNNSS``:

* ``DMS-``  fixed prefix
* ``YY``    last two digits of the received year (e.g. ``26`` for 2026)
* ``A``     alpha section, ``A``..``Z`` — advances when ``NNN`` rolls past 999
* ``NNN``   per-year counter, ``001``..``999`` (resets each year)
* ``SS``    two base-36 checksum characters

The eight *significant* characters are ``YYANNNSS``. The six-character
*core* ``YYANNN`` feeds a deterministic checksum whose only job is to catch
human mis-reads/typos of the **printed** tag. Scanning still resolves an
asset by its UUID (the QR payload is unchanged), so the checksum never sits
on the scan path.

The atomic per-year counter lives on the :class:`inventory.models.AssetTagSequence`
model; :func:`generate_asset_tag` composes the pieces. Pure checksum/validation
helpers here take no database dependency so they are cheap to unit test and
safe to call from anywhere (serializers, admin, scanners).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.db import IntegrityError
from django.utils import timezone

if TYPE_CHECKING:
    from inventory.models.asset import Asset

# Prefix that every asset tag carries.
TAG_PREFIX = "DMS-"

# Number of base-36 characters in the checksum.
CHECKSUM_LEN = 2

# 36 ** CHECKSUM_LEN — the modulus the weighted sum is reduced into.
_CHECKSUM_MODULUS = 36**CHECKSUM_LEN

# base-36 alphabet used to encode the checksum (0-9 then A-Z).
_BASE36_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# ``YYANNN`` — two digits, one letter, three digits — followed by the
# two-character checksum. Anchored + upper-case only.
_TAG_RE = re.compile(r"^DMS-(\d{2}[A-Z]\d{3})([0-9A-Z]{2})$")


def _to_base36(value: int, width: int) -> str:
    """Encode ``value`` as a zero-padded, fixed-``width`` base-36 string."""
    digits = []
    for _ in range(width):
        value, rem = divmod(value, 36)
        digits.append(_BASE36_ALPHABET[rem])
    return "".join(reversed(digits))


def compute_asset_tag_checksum(core: str) -> str:
    """Return the two-character base-36 checksum for a tag *core*.

    ``core`` is the six significant characters ``YYANNN``. Each character is
    read as a base-36 digit and multiplied by a distinct positional weight;
    the weighted sum is reduced modulo ``36**2`` and encoded as two base-36
    characters. Distinct per-position weights mean any single-character
    substitution and any transposition of two characters changes the result,
    so a mis-typed printed tag fails :func:`validate_asset_tag`.
    """
    core = core.upper()
    total = 0
    for position, char in enumerate(core, start=1):
        try:
            total += int(char, 36) * position
        except ValueError as exc:  # pragma: no cover - guards misuse
            raise ValueError(f"Invalid character {char!r} in asset tag core {core!r}") from exc
    return _to_base36(total % _CHECKSUM_MODULUS, CHECKSUM_LEN)


def validate_asset_tag(tag: str) -> bool:
    """Return ``True`` iff ``tag`` is a well-formed ``DMS-YYANNNSS`` tag.

    Validates both the structural shape and the checksum. Legacy random tags
    (``DMS-<8 hex>``) and factory placeholders (``AST-00001``) return
    ``False`` — this only recognises the new format.
    """
    if not isinstance(tag, str):
        return False
    match = _TAG_RE.match(tag.strip().upper())
    if not match:
        return False
    core, checksum = match.group(1), match.group(2)
    return compute_asset_tag_checksum(core) == checksum


def compose_asset_tag(core: str) -> str:
    """Return the full ``DMS-YYANNNSS`` tag for a six-character *core*."""
    return f"{TAG_PREFIX}{core}{compute_asset_tag_checksum(core)}"


def generate_asset_tag(year: int) -> str:
    """Allocate the next per-year sequence value and return a full tag.

    Advances the atomic :class:`inventory.models.AssetTagSequence` counter for
    ``year`` (under ``select_for_update``) and composes the checksummed tag.
    Imported lazily to avoid a models <-> services import cycle.
    """
    from inventory.models import AssetTagSequence

    core = AssetTagSequence.allocate_core(year)
    return compose_asset_tag(core)


def assign_asset_tag(asset: "Asset", *, max_retries: int = 10) -> str:
    """Assign a unique asset tag to ``asset`` when it does not already have one.

    Returns the tag. A no-op — and never a regeneration — when
    ``asset.asset_tag`` is already set, so an existing tag survives repeated
    saves. The received year drives the ``YY`` segment (see
    :func:`generate_asset_tag`) and falls back to the current year when
    ``date_received`` is unset.

    The atomic per-year sequence already guarantees uniqueness, but we still
    retry on the off chance a freshly allocated tag collides with a backfilled
    one; after ``max_retries`` consecutive collisions we raise
    :class:`~django.db.IntegrityError`.

    Extracted from ``Asset.save()`` (gh #887) so bare ``Asset(...).save()``
    callers (admin duplicate action, ``epaper_preview`` command) keep getting a
    tag generated synchronously on first save.
    """
    if asset.asset_tag:
        return asset.asset_tag

    from inventory.models import Asset

    year = asset.date_received.year if asset.date_received else timezone.now().year
    for _ in range(max_retries):
        candidate = generate_asset_tag(year)
        if not Asset.objects.filter(asset_tag=candidate).exists():
            asset.asset_tag = candidate
            return asset.asset_tag
    # pragma: no cover - only if max_retries consecutive allocations collide
    raise IntegrityError("Could not allocate a unique asset tag")


__all__ = [
    "TAG_PREFIX",
    "CHECKSUM_LEN",
    "compute_asset_tag_checksum",
    "validate_asset_tag",
    "compose_asset_tag",
    "generate_asset_tag",
    "assign_asset_tag",
]
