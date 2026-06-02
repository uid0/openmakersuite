"""BMS adapter abstraction + concrete implementations.

Public surface:

* ``BmsAdapter`` — ABC every adapter implements.
* ``BmsAdapterError`` — exception type adapters raise for any failure.
* ``BmsThermostatInfo`` / ``BmsThermostatState`` — dataclasses returned
  by ``list_thermostats`` / ``get_state``.
* ``adapter_for(config)`` — resolves a ``BmsConfig`` row to a concrete
  adapter instance. Used by management commands and (later) the
  Celery beat sync task. Test code can monkeypatch this to inject
  ``MockAdapter`` without touching every call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BmsAdapter, BmsAdapterError, BmsThermostatInfo, BmsThermostatState
from .mock import MockAdapter
from .resideo import ResideoAdapter

if TYPE_CHECKING:
    from ..models import BmsConfig


def adapter_for(config: "BmsConfig") -> BmsAdapter:
    """Resolve a ``BmsConfig.adapter_type`` to a concrete adapter.

    MockAdapter is intentionally returned with no preset state — callers
    that want to fake responses should construct MockAdapter directly,
    not go through this resolver. Resolving to a bare mock here would
    silently no-op a misconfigured prod row.
    """
    from ..models import BmsConfig as BmsConfigModel

    if config.adapter_type == BmsConfigModel.ADAPTER_RESIDEO:
        return ResideoAdapter(config)
    if config.adapter_type == BmsConfigModel.ADAPTER_MOCK:
        return MockAdapter()
    raise BmsAdapterError(
        f"Unknown BMS adapter_type {config.adapter_type!r} on config " f"{config.name!r}"
    )


__all__ = [
    "BmsAdapter",
    "BmsAdapterError",
    "BmsThermostatInfo",
    "BmsThermostatState",
    "MockAdapter",
    "ResideoAdapter",
    "adapter_for",
]
