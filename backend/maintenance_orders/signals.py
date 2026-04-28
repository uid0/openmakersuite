"""Signals wiring the warranty gate onto third-party WO creation.

When a ThirdPartyWorkOrder is opened against an asset that has an active
warranty (today within install_date..end_date), set ``warranty_recovery=True``
so Logistics knows to chase the manufacturer for reimbursement before
absorbing the vendor invoice. A logger event is emitted so Phase 5's
auto-generated Recovery Task pipeline has something to consume.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import AssetWarranty, ThirdPartyWorkOrder

logger = logging.getLogger("maintenance_orders.warranty_gate")


def active_warranty_for(asset) -> AssetWarranty | None:
    """Return the asset's currently-active warranty, if any.

    'Active' = today is within [install_date, end_date]. If multiple warranties
    overlap (rare but valid for stacked manufacturer + extended coverage), the
    one ending latest wins so the WO defers reimbursement chase as long as
    possible.
    """
    if asset is None:
        return None
    today = timezone.now().date()
    qs = AssetWarranty.objects.filter(
        asset=asset,
        install_date__lte=today,
        end_date__gte=today,
    ).order_by("-end_date")
    return qs.first()


@receiver(post_save, sender=ThirdPartyWorkOrder, dispatch_uid="warranty_gate_on_wo_create")
def apply_warranty_gate(sender, instance: ThirdPartyWorkOrder, created: bool, **kwargs) -> None:
    if not created or instance.asset is None:
        return
    warranty = active_warranty_for(instance.asset)
    if warranty is None:
        return
    if not instance.warranty_recovery:
        # Persist the flag without re-triggering the post_save handler.
        ThirdPartyWorkOrder.objects.filter(pk=instance.pk).update(warranty_recovery=True)
        instance.warranty_recovery = True
    logger.info(
        "warranty_gate: WO=%s asset=%s warranty=%s provider=%s end=%s -> recovery flagged",
        instance.short_id,
        instance.asset_id,
        warranty.id,
        warranty.provider,
        warranty.end_date,
    )
