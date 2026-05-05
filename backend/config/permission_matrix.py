"""Permission matrix introspection + drift detection (gh #328).

Walks the DRF URL conf and snapshots ``permission_classes`` for every
URL-routed view. The snapshot is compared to ``api_permission_matrix.yaml``
(the source of truth) so PRs that change ``permission_classes`` without
updating the matrix fail CI.

See ``docs/API_PERMISSION_MATRIX.md`` for the human-readable matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from django.urls import URLPattern, URLResolver, get_resolver

import yaml

MATRIX_PATH = Path(__file__).resolve().parent / "api_permission_matrix.yaml"

# URL prefixes we audit. ``flower/`` is a superuser-only proxy mounted at the
# project root; everything else under ``api/`` is project DRF.
AUDITED_PREFIXES = ("api/", "flower/")

# Some views are routed through ``/api/`` but are out of scope for the
# matrix (third-party packages, schema generators, etc.). Each entry is
# matched against the dotted view path.
EXCLUDED_VIEW_PREFIXES = (
    # OpenAPI surface — static metadata, no business permissions.
    "drf_spectacular.",
    # DRF browsable-API login/logout under ``/api/token/``.
    "django.contrib.auth.views.",
    # Auto-generated DRF router root; permission is whatever the DRF default
    # provides and there is no business surface to classify.
    "rest_framework.routers.APIRootView",
)


@dataclass(frozen=True)
class EndpointKey:
    """Identifier for a unique (view, action) pair routed by DRF.

    For function-based views and ``APIView`` subclasses, ``action`` is None.
    For ``ViewSet`` subclasses, ``action`` is the bound action name (``list``,
    ``retrieve``, ``scan``, etc.).
    """

    view_path: str
    action: str | None

    def __str__(self) -> str:
        return f"{self.view_path}#{self.action or '*'}"


@dataclass
class EndpointSnapshot:
    """The permission_classes for a single (view, action) pair, plus an
    example URL pattern for human-readable diff messages."""

    key: EndpointKey
    permission_classes: tuple[str, ...]
    example_pattern: str
    example_method: str


def _perm_names(view_cls, action_name: str | None) -> tuple[str, ...]:
    """Return the permission_classes for ``view_cls``, taking any per-action
    override from a DRF ``@action`` decorator into account."""
    if action_name is not None:
        method = getattr(view_cls, action_name, None)
        # ``@action(permission_classes=[...])`` stashes its kwargs on the bound
        # function so we can read them without instantiating the ViewSet.
        if method is not None and hasattr(method, "kwargs"):
            override = method.kwargs.get("permission_classes")
            if override is not None:
                return tuple(getattr(c, "__name__", repr(c)) for c in override)
    pc = getattr(view_cls, "permission_classes", None)
    if pc is None:
        return ("<default>",)
    return tuple(getattr(c, "__name__", repr(c)) for c in pc)


def _walk(resolver, prefix: str) -> Iterable[EndpointSnapshot]:
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            yield from _walk(entry, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            full = prefix + str(entry.pattern)
            if not full.startswith(AUDITED_PREFIXES):
                continue
            cb = entry.callback
            view_cls = getattr(cb, "cls", None) or getattr(cb, "view_class", None) or cb
            view_path = (
                f"{getattr(view_cls, '__module__', '?')}." f"{getattr(view_cls, '__name__', '?')}"
            )
            if view_path.startswith(EXCLUDED_VIEW_PREFIXES):
                continue
            actions = getattr(cb, "actions", None) or {}
            if actions:
                # ViewSet routes — one entry per action name.
                seen: set[str] = set()
                for method, action_name in actions.items():
                    if action_name in seen:
                        continue
                    seen.add(action_name)
                    yield EndpointSnapshot(
                        key=EndpointKey(view_path, action_name),
                        permission_classes=_perm_names(view_cls, action_name),
                        example_pattern=full,
                        example_method=method.upper(),
                    )
            else:
                yield EndpointSnapshot(
                    key=EndpointKey(view_path, None),
                    permission_classes=_perm_names(view_cls, None),
                    example_pattern=full,
                    example_method="*",
                )


def introspect_endpoints() -> dict[EndpointKey, EndpointSnapshot]:
    """Return the actual ``permission_classes`` snapshot for every audited
    URL-routed DRF endpoint, keyed by ``(view_path, action)``."""
    out: dict[EndpointKey, EndpointSnapshot] = {}
    for snap in _walk(get_resolver(), ""):
        # If the same (view, action) pair appears under multiple URL prefixes
        # (DRF routers register both the bare path and a ``.<format>`` path),
        # keep the first occurrence. Permission classes will be identical.
        out.setdefault(snap.key, snap)
    return out


def load_matrix(path: Path = MATRIX_PATH) -> dict[EndpointKey, tuple[str, ...]]:
    """Load the YAML matrix and return ``{EndpointKey: permission_classes}``.

    The YAML format is a single top-level ``views`` mapping, e.g.::

        views:
          inventory.views.SupplierViewSet:
            permission_classes: [IsAuthenticatedOrReadOnly]
            actions:
              scan:
                permission_classes: [AllowAny]
    """
    raw = yaml.safe_load(path.read_text())
    out: dict[EndpointKey, tuple[str, ...]] = {}
    for view_path, entry in (raw.get("views") or {}).items():
        if "permission_classes" in entry:
            out[EndpointKey(view_path, None)] = tuple(entry["permission_classes"])
        for action_name, action_entry in (entry.get("actions") or {}).items():
            out[EndpointKey(view_path, action_name)] = tuple(action_entry["permission_classes"])
    return out


def diff(
    actual: dict[EndpointKey, EndpointSnapshot],
    expected: dict[EndpointKey, tuple[str, ...]],
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(missing_in_yaml, stale_in_yaml, mismatched)`` as human-readable
    bullet lines. Empty lists mean the matrix is in sync with code."""
    missing = []
    stale = []
    mismatched = []
    for key, snap in sorted(actual.items(), key=lambda kv: str(kv[0])):
        if key not in expected:
            missing.append(
                f"  - {key} ({snap.example_method} {snap.example_pattern}) "
                f"actual=[{', '.join(snap.permission_classes)}]"
            )
            continue
        if expected[key] != snap.permission_classes:
            mismatched.append(
                f"  - {key} ({snap.example_method} {snap.example_pattern})\n"
                f"      expected: [{', '.join(expected[key])}]\n"
                f"      actual:   [{', '.join(snap.permission_classes)}]"
            )
    for key in sorted(expected, key=str):
        if key not in actual:
            stale.append(f"  - {key} (no longer routed; remove from YAML)")
    return missing, stale, mismatched
