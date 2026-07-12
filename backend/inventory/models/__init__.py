"""Inventory models package.

Re-exports every public model and helper so ``from inventory.models import X``
keeps working for all existing importers after the split into submodules.
"""

from .asset import (  # noqa: F401
    Asset,
    AssetDocument,
    AssetMeter,
    AssetMeterReading,
    AssetOutOfService,
    AssetPart,
    AssetProblem,
    AssetProblemPhoto,
    AssetReservation,
    AssetTagSequence,
)
from .core import (  # noqa: F401
    Category,
    ComponentUsageEvent,
    InventoryItem,
    ItemSupplier,
    Location,
    PriceHistory,
    SerializedComponent,
    StockReconciliation,
    Supplier,
    UsageLog,
    generate_sku,
)
from .fixtures import (  # noqa: F401
    Fixture,
    FixtureRefillRequest,
)
from .location_problem import (  # noqa: F401
    LocationProblem,
)
from .maintenance import (  # noqa: F401
    MaintenanceAuditEvent,
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceLogPhoto,
    MaintenanceMaterial,
    MaintenanceRecord,
    MaintenanceTask,
    MaintenanceTool,
    WorkOrder,
    WorkOrderMaterialUsage,
    WorkOrderOmrTemplate,
    WorkOrderPhoto,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
    WorkOrderValidation,
)

__all__ = [
    "generate_sku",
    "Location",
    "Supplier",
    "Category",
    "InventoryItem",
    "ItemSupplier",
    "PriceHistory",
    "UsageLog",
    "StockReconciliation",
    "SerializedComponent",
    "ComponentUsageEvent",
    "AssetTagSequence",
    "Asset",
    "AssetPart",
    "AssetProblem",
    "AssetProblemPhoto",
    "AssetDocument",
    "AssetMeter",
    "AssetMeterReading",
    "AssetReservation",
    "AssetOutOfService",
    "LocationProblem",
    "MaintenanceItem",
    "MaintenanceMaterial",
    "MaintenanceTool",
    "MaintenanceLog",
    "MaintenanceLogPhoto",
    "MaintenanceTask",
    "WorkOrder",
    "WorkOrderTaskCompletion",
    "WorkOrderMaterialUsage",
    "WorkOrderPhoto",
    "WorkOrderValidation",
    "WorkOrderOmrTemplate",
    "WorkOrderSubmission",
    "MaintenanceAuditEvent",
    "MaintenanceRecord",
    "Fixture",
    "FixtureRefillRequest",
]
