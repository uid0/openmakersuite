"""
Celery tasks for inventory management.
"""

import logging

from django.apps import apps
from django.core.files.base import ContentFile

import requests
from celery import shared_task

logger = logging.getLogger(__name__)

# Metadata marker + display cap for the nightly reorder-alert digest (op-2).
REORDER_DIGEST_KIND = "demand_forecast_reorder_alert"
MAX_DIGEST_ITEMS = 20


@shared_task
def download_image_from_url(item_id, image_url):
    """
    Asynchronous task to download image from URL for an item.

    Args:
        item_id: UUID string of the inventory item
        image_url: URL to download image from
    """
    InventoryItem = apps.get_model("inventory", "InventoryItem")

    try:
        item = InventoryItem.objects.get(id=item_id)

        # Don't download if image already exists
        if item.image:
            return f"Image already exists for {item.name}"

        # Download the image
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()

        # Determine file extension from content type or URL
        content_type = response.headers.get("content-type", "")
        if "webp" in content_type:
            ext = "webp"
        elif "png" in content_type:
            ext = "png"
        elif "jpeg" in content_type or "jpg" in content_type:
            ext = "jpg"
        else:
            # Try to get from URL
            ext = image_url.split(".")[-1].split("?")[0].lower()
            if ext not in ["jpg", "jpeg", "png", "webp"]:
                ext = "jpg"  # default

        # Save the downloaded image
        image_content = ContentFile(response.content)
        item.image.save(
            f"{item.sku or item.id}.{ext}",
            image_content,
            save=True,  # This will save the item
        )

        return f"Image downloaded for {item.name} from {image_url}"

    except InventoryItem.DoesNotExist:
        return f"Item {item_id} not found"
    except requests.RequestException as e:
        return f"Failed to download image for item {item_id}: {str(e)}"
    except Exception as e:
        return f"Error processing image for item {item_id}: {str(e)}"


@shared_task
def generate_qr_code(item_id):
    """
    Asynchronous task to generate QR code for an item.

    Args:
        item_id: UUID string of the inventory item
    """
    from .services.qr_code_service import QRCodeService

    InventoryItem = apps.get_model("inventory", "InventoryItem")

    try:
        item = InventoryItem.objects.get(id=item_id)
        service = QRCodeService(include_logo=True)  # Include logo by default
        service.generate_for_item(item)
        return f"QR code generated for {item.name}"
    except InventoryItem.DoesNotExist:
        return f"Item {item_id} not found"


@shared_task
def generate_index_card(item_id):
    """
    Asynchronous task to generate index card PDF for an item.

    Args:
        item_id: UUID string of the inventory item
    """
    from .utils.pdf_generator import generate_item_card

    InventoryItem = apps.get_model("inventory", "InventoryItem")

    try:
        item = InventoryItem.objects.get(id=item_id)
        generate_item_card(item)
        # TODO: Save to file storage or send via email
        return f"Index card generated for {item.name}"
    except InventoryItem.DoesNotExist:
        return f"Item {item_id} not found"


@shared_task
def update_average_lead_times():
    """
    Periodic task to update average lead times based on historical data.

    Updates ItemSupplier average_lead_time based on completed reorders.
    """
    from datetime import timedelta

    from django.utils import timezone

    ReorderRequest = apps.get_model("reorder_queue", "ReorderRequest")
    ItemSupplier = apps.get_model("inventory", "ItemSupplier")

    # Calculate lead times for completed orders in the last 6 months
    six_months_ago = timezone.now() - timedelta(days=180)

    item_suppliers = ItemSupplier.objects.filter(is_active=True)
    updated_count = 0

    for item_supplier in item_suppliers:
        # Get completed reorders for this item from this supplier
        # Note: ReorderRequest would need a supplier field to track which supplier was used
        # For now, we'll update based on all reorders for the item
        completed_reorders = ReorderRequest.objects.filter(
            item=item_supplier.item,
            status=ReorderRequest.Status.RECEIVED,
            ordered_at__isnull=False,
            actual_delivery__isnull=False,
            ordered_at__gte=six_months_ago,
        )

        if completed_reorders.exists():
            # Calculate average lead time in days
            total_days = 0
            count = 0

            for reorder in completed_reorders:
                lead_time = (reorder.actual_delivery - reorder.ordered_at.date()).days
                if lead_time > 0:
                    total_days += lead_time
                    count += 1

            if count > 0:
                item_supplier.average_lead_time = total_days // count
                item_supplier.save(update_fields=["average_lead_time"])
                updated_count += 1

    return f"Updated lead times for {updated_count} items"


@shared_task
def roll_up_meters():
    """Beat task: advance every active AssetMeter from its source (EAM bead-1).

    Thin wrapper over :func:`inventory.services.meter_sources.run_rollup` so the
    rollup logic lives in the service layer (single source of truth, unit-testable
    without Celery) and this task only handles scheduling. Runs every 15 minutes
    via ``CELERY_BEAT_SCHEDULE`` — folds ended usage sessions into runtime-hour
    readings (exactly-once via each meter's watermark) and dual-writes
    ``Asset.hours_used`` so the maintenance forecast keeps working untouched.
    """
    from .services.meter_sources import run_rollup

    return run_rollup()


@shared_task
def generate_demand_forecasts():
    """Beat task (nightly 04:00): forecast restock timing for non-serialized items.

    For every active, non-retired, **non-serialized** item, collect its
    purchase-event dates, average the gaps between them, and persist **one**
    :class:`~inventory.models.DemandForecast` row predicting when the item is
    due to be bought again (history is retained — the model keeps every run).
    The forecasting maths live in
    :mod:`inventory.services.demand_forecast_engine`; this task only iterates,
    persists, and — per item — isolates failures so one bad item can't sink the
    run.

    After the rows are written it emits a single in-app reorder-alert digest
    (see :func:`_emit_reorder_alert_digest`) for opted-in items that are due
    within their lead time, deduped per run-date.
    """
    from django.utils import timezone

    # Reuse the serialized forecast's batched lead-time resolution (observed
    # LeadTimeLog mean, else the primary supplier's estimate) to avoid N+1.
    from .services.component_forecast import _lead_time_days_by_item
    from .services.demand_forecast_engine import build_restock_events, forecast_item_by_interval

    InventoryItem = apps.get_model("inventory", "InventoryItem")
    DemandForecast = apps.get_model("inventory", "DemandForecast")

    now = timezone.now()
    end = now.date()

    items = list(
        # Kits are excluded (op-8n0): a kit carries no stock of its own -- its
        # receipts credit its components -- so forecasting demand for one would
        # model a permanently-zero series and recommend restocking a thing that
        # is never stocked.
        InventoryItem.objects.filter(
            is_active=True, is_retired=False, is_serialized=False, is_kit=False
        )
    )
    lead_by_item = _lead_time_days_by_item(items) if items else {}

    created = 0
    failed = 0
    for item in items:
        try:
            events = build_restock_events(item, end=end)
            lead = lead_by_item.get(item.id)
            result = forecast_item_by_interval(item, events, now=now, lead_time_days=lead)
            DemandForecast.objects.create(
                item=item,
                generated_at=now,
                avg_interval_days=result.avg_interval_days,
                interval_samples=result.interval_samples,
                last_restock_date=result.last_restock_date,
                predicted_next_reorder_date=result.predicted_next_reorder_date,
                days_until_due=result.days_until_due,
                horizon_days=result.horizon_days,
                predicted_daily_demand=result.predicted_daily_demand,
                horizon_demand=result.horizon_demand,
                horizon_demand_upper=result.horizon_demand_upper,
                available_at_generation=result.available_at_generation,
                days_until_stockout=result.days_until_stockout,
                projected_stockout_date=result.projected_stockout_date,
                predictive_reorder_point=result.predictive_reorder_point,
                needs_reorder=result.needs_reorder,
                lead_time_days=result.lead_time_days,
                safety_stock=result.safety_stock,
                method=result.method,
                model_version=result.model_version,
            )
            created += 1
        except Exception:
            failed += 1
            logger.exception("Demand forecast failed for item %s", getattr(item, "id", "?"))

    alerts = _emit_reorder_alert_digest(now=now)
    return (
        f"Demand forecasts: {created} created, {failed} failed; "
        f"reorder-alert digest notifications: {alerts}"
    )


@shared_task
def snapshot_stock_levels():
    """Beat task (weekly): snapshot every tracked item's ``current_stock``.

    For every active, non-retired :class:`~inventory.models.InventoryItem` this
    writes **one** :class:`~inventory.models.StockLevelSnapshot` per calendar
    week, keyed on the week-start (Monday) date. Using the week anchor makes the
    run idempotent: a re-run within the same week ``update_or_create``s the
    existing row (refreshing the count) instead of appending a duplicate point.
    There is no historical backfill — the series starts accumulating from the
    first run.

    Read by the ``stock_history`` action on
    :class:`inventory.views.InventoryItemViewSet`, which powers the Inventory
    Stock-History chart.
    """
    from datetime import timedelta

    from django.utils import timezone

    InventoryItem = apps.get_model("inventory", "InventoryItem")
    StockLevelSnapshot = apps.get_model("inventory", "StockLevelSnapshot")

    today = timezone.now().date()
    # Anchor to the Monday of the current week so the weekly cadence is stable
    # and any re-run lands on the same (item, snapshot_date) key.
    week_start = today - timedelta(days=today.weekday())

    created = 0
    updated = 0
    # Kits hold no stock, so a snapshot row for one would record a flat zero
    # forever and skew every "stock over time" chart it appears on (op-8n0).
    for item in InventoryItem.objects.filter(
        is_active=True, is_retired=False, is_kit=False
    ).iterator():
        _, was_created = StockLevelSnapshot.objects.update_or_create(
            item=item,
            snapshot_date=week_start,
            defaults={"count": item.current_stock},
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return f"Stock snapshots for {week_start}: {created} created, {updated} updated"


def _due_phrase(days):
    """Humanise a forecast's ``days_until_due`` for the digest listing."""
    if days is None:
        return "due"
    days = int(days)
    if days < 0:
        return f"overdue by {-days}d"
    if days == 0:
        return "due today"
    return f"due in {days}d"


def _emit_reorder_alert_digest(*, now):
    """Emit ONE in-app reorder-alert digest to admins, deduped per run-date.

    The notify set is :func:`reorder_alert_forecasts` — the latest row per item
    for items whose owner opted in (``reorder_alerts_enabled``) **and** that are
    due to be bought within their lead time (``needs_reorder``). A single
    ``warning`` notification per admin lists them; a ``metadata`` marker
    (``kind`` + ``run_date``) makes a second run on the same day a no-op so the
    digest isn't re-sent. Returns the number of notifications created.
    """
    from notifications.models import Notification
    from notifications.services import notify_admins

    from .services.demand_forecast import reorder_alert_forecasts

    forecasts = reorder_alert_forecasts()
    if not forecasts:
        return 0

    run_date = now.date().isoformat()
    already_sent = Notification.objects.filter(
        metadata__kind=REORDER_DIGEST_KIND, metadata__run_date=run_date
    ).exists()
    if already_sent:
        return 0

    shown = forecasts[:MAX_DIGEST_ITEMS]
    listing = "; ".join(f"{f.item.name} ({_due_phrase(f.days_until_due)})" for f in shown)
    extra = len(forecasts) - len(shown)
    if extra > 0:
        listing += f"; and {extra} more"

    count = len(forecasts)
    notifications = notify_admins(
        type="warning",
        title=f"{count} watched item{'' if count == 1 else 's'} due for reorder",
        message=(
            "Predictive reorder alert — items due to be reordered based on how "
            f"often they are normally bought: {listing}."
        ),
        action_url="/inventory/admin",
        metadata={
            "kind": REORDER_DIGEST_KIND,
            "run_date": run_date,
            "item_count": count,
            "item_ids": [str(f.item_id) for f in forecasts],
        },
    )
    return len(notifications)
