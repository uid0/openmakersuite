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
    InventorySafetyProfile,
    ItemSupplier,
    Location,
    PackagingLevel,
    PriceHistory,
    SerializedComponent,
    StockLevelSnapshot,
    StockReconciliation,
    Supplier,
    SupplierAgreement,
    UsageLog,
    generate_sku,
)
from .fixtures import (  # noqa: F401
    Fixture,
    FixtureRefillRequest,
)
from .forecasting import (  # noqa: F401
    DemandForecast,
)
from .kit import (  # noqa: F401
    KitComponent,
)
from .location_problem import (  # noqa: F401
    LocationProblem,
)
from .maintenance import (  # noqa: F401
    ElapsedTimerModel,
    MaintenanceAuditEvent,
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceLogPhoto,
    MaintenanceMaterial,
    MaintenanceRecord,
    MaintenanceTask,
    MaintenanceTool,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderLotoCompletion,
    WorkOrderMaterialUsage,
    WorkOrderOmrTemplate,
    WorkOrderPhoto,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
    WorkOrderValidation,
)
from .typed_target import (  # noqa: F401
    TargetField,
    TypedTargetModel,
    count_present_targets,
)

__all__ = [
    "generate_sku",
    "Location",
    "Supplier",
    "SupplierAgreement",
    "Category",
    "InventoryItem",
    "InventorySafetyProfile",
    "ItemSupplier",
    "PackagingLevel",
    "PriceHistory",
    "UsageLog",
    "StockReconciliation",
    "StockLevelSnapshot",
    "SerializedComponent",
    "ComponentUsageEvent",
    "KitComponent",
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
    "ElapsedTimerModel",
    "WorkOrder",
    "WorkOrderAttachment",
    "WorkOrderTaskCompletion",
    "WorkOrderLotoCompletion",
    "WorkOrderMaterialUsage",
    "WorkOrderPhoto",
    "WorkOrderValidation",
    "WorkOrderOmrTemplate",
    "WorkOrderSubmission",
    "MaintenanceAuditEvent",
    "MaintenanceRecord",
    "Fixture",
    "FixtureRefillRequest",
    "DemandForecast",
    "TargetField",
    "TypedTargetModel",
    "count_present_targets",
]
