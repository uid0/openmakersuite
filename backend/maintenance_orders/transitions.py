"""Hand-rolled state machine for ThirdPartyWorkOrder.

Phase 3: implements the 7-step operational workflow as a series of named
transitions. Each transition is a callable that:

  1. Validates the current state allows the transition.
  2. Validates business preconditions (3 quotes, photo evidence, etc).
  3. Validates the actor has the required permission.
  4. Applies side-effects (timestamps, audit rows, notifications).
  5. Persists the new status.

Why hand-rolled rather than django-fsm: the project deliberately avoids new
runtime deps (see AGENTS.md), and the side-effect surface here is small
enough that explicit functions read more clearly than decorators.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from membership.utils import is_logistics_member, is_sig_admin

from .models import EmergencyAuthorization, ThirdPartyWorkOrder, ThirdPartyWorkOrderAttachment

REQUIRED_QUOTE_COUNT = 3
"""Number of vendor quotes a non-emergency standard WO must have before scheduling."""

VARIANCE_RELATIVE_CAP = Decimal("0.15")
"""Maximum variance as fraction of NTE before closure is blocked."""

EMERGENCY_AUTH_VALID_FOR = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------


def _is_staff_or_super(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def _can_admin_wo(user, wo: ThirdPartyWorkOrder) -> bool:
    """SIG admin (of the asset's owning group), Logistics, or staff."""
    if _is_staff_or_super(user):
        return True
    if is_logistics_member(user):
        return True
    if wo.asset is not None and getattr(wo.asset, "owning_group_id", None):
        return is_sig_admin(user, wo.asset.owning_group)
    return False


def _ensure_actor(user, wo: ThirdPartyWorkOrder) -> None:
    if not _can_admin_wo(user, wo):
        raise PermissionDenied(
            "Only SIG admins, Logistics, or staff may transition this work order."
        )


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def has_active_emergency_authorization(wo: ThirdPartyWorkOrder) -> bool:
    """Return True if a non-revoked emergency auth row is < 24h old."""
    now = timezone.now()
    return wo.emergency_authorizations.filter(
        revoked_at__isnull=True,
        expires_at__gt=now,
    ).exists()


def has_photo_evidence(wo: ThirdPartyWorkOrder) -> bool:
    return wo.attachments.filter(kind=ThirdPartyWorkOrderAttachment.KIND_PHOTO).exists()


def has_required_quotes(wo: ThirdPartyWorkOrder) -> bool:
    """3 quotes OR a signed waiver OR an emergency bypass."""
    if wo.is_emergency or has_active_emergency_authorization(wo):
        return True
    if wo.quote_waiver_signed_by_id:
        return True
    return wo.quotes.count() >= REQUIRED_QUOTE_COUNT


def has_invoice_and_fsr(wo: ThirdPartyWorkOrder) -> bool:
    kinds = set(wo.attachments.values_list("kind", flat=True))
    return (
        ThirdPartyWorkOrderAttachment.KIND_INVOICE in kinds
        and ThirdPartyWorkOrderAttachment.KIND_FSR in kinds
    )


# ---------------------------------------------------------------------------
# Variance / dispatch fee helpers
# ---------------------------------------------------------------------------


def evaluate_variance(wo: ThirdPartyWorkOrder) -> str:
    """Return 'auto_approved' or 'blocked' based on actual vs. NTE.

    Variance is auto-approved iff:
      - actual_invoice_total <= nte_amount + par_cost_buffer, AND
      - actual_invoice_total <= nte_amount * (1 + 15%)

    Otherwise blocked. Emergency WOs without an NTE always pass through to
    financial review for human sign-off (variance_status stays empty,
    closure proceeds without auto-approval — Finance must explicitly close).
    """
    if wo.nte_amount is None or wo.actual_invoice_total is None:
        return ""
    overage = wo.actual_invoice_total - wo.nte_amount
    if overage <= Decimal("0"):
        return "auto_approved"
    relative_cap = wo.nte_amount * VARIANCE_RELATIVE_CAP
    par = wo.par_cost_buffer or Decimal("0")
    if overage <= par and overage <= relative_cap:
        return "auto_approved"
    return "blocked"


def split_dispatch_fee(wo: ThirdPartyWorkOrder) -> None:
    """Materialize per-asset allocated_cost on the asset_links rows.

    Algorithm:
      flat = dispatch_fee or 0
      net  = (actual_invoice_total or 0) - flat
      per-asset cost = net * share_pct/100  +  flat / N

    If share_pct rows are absent, default to equal split across all linked
    assets. The dispatch fee is the "flat truck-roll" that gets split equally
    regardless of share_pct (the bead specifies "default equal").
    """
    links = list(wo.asset_links.all())
    if not links:
        return
    n = Decimal(len(links))
    flat = wo.dispatch_fee or Decimal("0")
    invoice = wo.actual_invoice_total or Decimal("0")
    net = invoice - flat
    flat_share = (flat / n).quantize(Decimal("0.01")) if n else Decimal("0")
    total_share_pct = sum((link.share_pct or Decimal("0")) for link in links)
    use_equal = total_share_pct == Decimal("0")
    for link in links:
        if use_equal:
            asset_cost = (net / n).quantize(Decimal("0.01"))
        else:
            asset_cost = (net * (link.share_pct / Decimal("100"))).quantize(Decimal("0.01"))
        link.allocated_cost = asset_cost + flat_share
        link.save(update_fields=["allocated_cost"])


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


class TransitionError(ValidationError):
    """Raised when a transition is invalid for the current state or data."""


def _ensure_status(wo: ThirdPartyWorkOrder, expected: str) -> None:
    if wo.status != expected:
        raise TransitionError(f"Work order is in '{wo.status}' state; expected '{expected}'.")


@transaction.atomic
def set_nte(wo: ThirdPartyWorkOrder, *, user, nte_amount: Decimal) -> ThirdPartyWorkOrder:
    """Step 1 (intake) — SIG sets NTE."""
    _ensure_actor(user, wo)
    if wo.status not in (
        ThirdPartyWorkOrder.STATUS_REQUESTED,
        ThirdPartyWorkOrder.STATUS_SOURCING,
    ):
        raise TransitionError("NTE can only be set during intake or sourcing.")
    if nte_amount is None or Decimal(nte_amount) < Decimal("0"):
        raise TransitionError("NTE amount must be a non-negative number.")
    wo.nte_amount = Decimal(nte_amount)
    wo.nte_set_by = user
    wo.nte_set_at = timezone.now()
    wo.save(update_fields=["nte_amount", "nte_set_by", "nte_set_at", "updated_at"])
    return wo


@transaction.atomic
def authorize_emergency(
    wo: ThirdPartyWorkOrder, *, user, reason: str = ""
) -> EmergencyAuthorization:
    """Logistics emergency authorization — bypasses NTE and 3-quote gates.

    Creates an audit row valid for 24h. ``has_active_emergency_authorization``
    returns False once that window expires until a new row is created.
    """
    if not (_is_staff_or_super(user) or is_logistics_member(user)):
        raise PermissionDenied("Only Logistics or staff may authorize emergency bypass.")
    auth = EmergencyAuthorization.objects.create(
        work_order=wo,
        authorized_by=user,
        reason=reason,
        expires_at=timezone.now() + EMERGENCY_AUTH_VALID_FOR,
    )
    wo.is_emergency = True
    wo.emergency_authorized_by = user
    wo.emergency_authorized_at = auth.authorized_at
    wo.save(
        update_fields=[
            "is_emergency",
            "emergency_authorized_by",
            "emergency_authorized_at",
            "updated_at",
        ]
    )
    return auth


@transaction.atomic
def waive_quote_requirement(wo: ThirdPartyWorkOrder, *, user, reason: str) -> ThirdPartyWorkOrder:
    """Authorized waiver of the 3-quote sourcing rule."""
    _ensure_actor(user, wo)
    if not reason or not reason.strip():
        raise TransitionError("Waiver requires a written reason for audit.")
    wo.quote_waiver_signed_by = user
    wo.quote_waiver_signed_at = timezone.now()
    wo.quote_waiver_reason = reason.strip()
    wo.save(
        update_fields=[
            "quote_waiver_signed_by",
            "quote_waiver_signed_at",
            "quote_waiver_reason",
            "updated_at",
        ]
    )
    return wo


@transaction.atomic
def advance_to_sourcing(wo: ThirdPartyWorkOrder, *, user) -> ThirdPartyWorkOrder:
    """Step 1 → 2. Requires NTE OR active emergency authorization."""
    _ensure_actor(user, wo)
    _ensure_status(wo, ThirdPartyWorkOrder.STATUS_REQUESTED)
    if wo.nte_amount is None and not (wo.is_emergency or has_active_emergency_authorization(wo)):
        raise TransitionError(
            "Cannot advance to sourcing without an NTE or emergency authorization."
        )
    wo.status = ThirdPartyWorkOrder.STATUS_SOURCING
    wo.save(update_fields=["status", "updated_at"])
    return wo


@transaction.atomic
def advance_to_scheduled(wo: ThirdPartyWorkOrder, *, user) -> ThirdPartyWorkOrder:
    """Step 2 → 3. Enforces the 3-quote rule (or waiver / emergency)."""
    _ensure_actor(user, wo)
    _ensure_status(wo, ThirdPartyWorkOrder.STATUS_SOURCING)
    if not has_required_quotes(wo):
        raise TransitionError(
            "Standard work orders require 3 quotes OR a signed waiver OR an "
            "active emergency authorization before scheduling."
        )
    wo.status = ThirdPartyWorkOrder.STATUS_SCHEDULED
    wo.save(update_fields=["status", "updated_at"])
    _emit_scheduling_email(wo)
    return wo


@transaction.atomic
def vendor_arrived(
    wo: ThirdPartyWorkOrder,
    *,
    user,
    keyfob_id: str = "",
    shadow_user=None,
) -> ThirdPartyWorkOrder:
    """Step 3 → 4. 'Vendor Arrived' button. Starts downtime clock."""
    _ensure_actor(user, wo)
    _ensure_status(wo, ThirdPartyWorkOrder.STATUS_SCHEDULED)
    wo.status = ThirdPartyWorkOrder.STATUS_IN_PROGRESS
    wo.downtime_start = timezone.now()
    if keyfob_id:
        wo.keyfob_id = keyfob_id
    if shadow_user is not None:
        wo.shadow_user = shadow_user
    wo.save(
        update_fields=[
            "status",
            "downtime_start",
            "keyfob_id",
            "shadow_user",
            "updated_at",
        ]
    )
    return wo


@transaction.atomic
def ops_sign_off(wo: ThirdPartyWorkOrder, *, user) -> ThirdPartyWorkOrder:
    """Step 4 → 5. Ops sign-off. Stops downtime clock; requires photo evidence."""
    _ensure_actor(user, wo)
    _ensure_status(wo, ThirdPartyWorkOrder.STATUS_IN_PROGRESS)
    if not has_photo_evidence(wo):
        raise TransitionError("At least one photo attachment is required before validation.")
    if not wo.downtime_start:
        # In-progress without a start timestamp is a data integrity bug, not a
        # user-fixable validation error — fall back to "now" so Ops aren't
        # locked out of closing legitimate work.
        wo.downtime_start = timezone.now()
    wo.downtime_end = timezone.now()
    wo.total_downtime = wo.downtime_end - wo.downtime_start
    wo.status = ThirdPartyWorkOrder.STATUS_VALIDATED
    wo.save(
        update_fields=[
            "status",
            "downtime_start",
            "downtime_end",
            "total_downtime",
            "updated_at",
        ]
    )
    return wo


@transaction.atomic
def advance_to_financial_review(
    wo: ThirdPartyWorkOrder,
    *,
    user,
    actual_invoice_total: Optional[Decimal] = None,
    dispatch_fee: Optional[Decimal] = None,
) -> ThirdPartyWorkOrder:
    """Step 5 → 6. Capture invoice total + dispatch fee; evaluate variance."""
    _ensure_actor(user, wo)
    _ensure_status(wo, ThirdPartyWorkOrder.STATUS_VALIDATED)
    if actual_invoice_total is not None:
        wo.actual_invoice_total = Decimal(actual_invoice_total)
    if dispatch_fee is not None:
        wo.dispatch_fee = Decimal(dispatch_fee)
    if wo.actual_invoice_total is None:
        raise TransitionError("Actual invoice total must be set before financial review.")
    wo.variance_status = evaluate_variance(wo)
    wo.status = ThirdPartyWorkOrder.STATUS_FINANCIAL_REVIEW
    wo.save(
        update_fields=[
            "actual_invoice_total",
            "dispatch_fee",
            "variance_status",
            "status",
            "updated_at",
        ]
    )
    split_dispatch_fee(wo)
    if wo.variance_status == "blocked":
        _alert_variance_blocked(wo)
    return wo


@transaction.atomic
def close_work_order(wo: ThirdPartyWorkOrder, *, user) -> ThirdPartyWorkOrder:
    """Step 6 → 7. Requires invoice + FSR; variance must not be blocked."""
    _ensure_actor(user, wo)
    _ensure_status(wo, ThirdPartyWorkOrder.STATUS_FINANCIAL_REVIEW)
    if wo.variance_status == "blocked":
        raise TransitionError(
            "Variance exceeds par buffer and 15% NTE — closure blocked. "
            "Adjust the invoice, raise the NTE, or escalate to Finance."
        )
    if not has_invoice_and_fsr(wo):
        raise TransitionError(
            "Closure requires both invoice and FSR (Field Service Report) attachments."
        )
    wo.status = ThirdPartyWorkOrder.STATUS_CLOSED
    wo.closed_at = timezone.now()
    wo.save(update_fields=["status", "closed_at", "updated_at"])
    _emit_closure_alert(wo)
    return wo


# ---------------------------------------------------------------------------
# Side-effect emitters (notifications + email).
# Phase 3 wires the in-app notification path; email goes via Django's
# send_mail with whatever EMAIL_BACKEND is configured (locmem in tests).
# ---------------------------------------------------------------------------


def _ops_recipients():
    """Yield users who should receive scheduling / closure ops emails."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group

    User = get_user_model()
    try:
        ops_group = Group.objects.get(name="Logistics")
    except Group.DoesNotExist:
        return User.objects.none()
    return User.objects.filter(groups=ops_group, is_active=True)


def _sig_leader_and_finance_recipients(wo: ThirdPartyWorkOrder):
    """Yield SIG admins of the WO's asset group plus Finance group members."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group

    User = get_user_model()
    user_ids = set()

    # SIG admins of the asset's owning group.
    if wo.asset is not None and getattr(wo.asset, "owning_group_id", None):
        from membership.models import SIGAdmin

        user_ids.update(
            SIGAdmin.objects.filter(group=wo.asset.owning_group, is_active=True).values_list(
                "user_id", flat=True
            )
        )

    for group_name in ("Finance", "SIG Leaders"):
        try:
            grp = Group.objects.get(name=group_name)
            user_ids.update(grp.user_set.values_list("id", flat=True))
        except Group.DoesNotExist:
            continue

    return User.objects.filter(id__in=user_ids, is_active=True)


def _emit_scheduling_email(wo: ThirdPartyWorkOrder) -> None:
    """Step 3: notify Ops that a vendor is scheduled (site-access notice)."""
    from django.core.mail import send_mail

    recipients = list(_ops_recipients().values_list("email", flat=True))
    recipients = [e for e in recipients if e]
    if not recipients:
        return
    asset_label = wo.asset.name if wo.asset else "(no asset linked)"
    subject = f"[Vendor Scheduled] {wo.short_id} — {wo.vendor.name}"
    body = (
        f"{wo.vendor.name} has been scheduled for {wo.title}.\n"
        f"Asset: {asset_label}\n"
        f"Keyfob: {wo.keyfob_id or '(to be assigned)'}\n"
        f"Site-access required — Ops should expect arrival."
    )
    send_mail(subject, body, None, recipients, fail_silently=True)


def _alert_variance_blocked(wo: ThirdPartyWorkOrder) -> None:
    """Step 6: variance blocked — in-app notification + email to SIG + Finance."""
    from django.core.mail import send_mail

    from notifications.models import Notification

    recipients = list(_sig_leader_and_finance_recipients(wo))
    overage = (wo.actual_invoice_total or Decimal("0")) - (wo.nte_amount or Decimal("0"))
    title = f"Variance blocked: {wo.short_id}"
    message = (
        f"Invoice ${wo.actual_invoice_total} exceeds NTE ${wo.nte_amount} "
        f"(over by ${overage}). Closure is blocked until variance is "
        f"resolved or escalated."
    )
    for user in recipients:
        Notification.objects.create(
            user=user,
            type="warning",
            title=title,
            message=message,
            metadata={
                "work_order_id": str(wo.id),
                "short_id": wo.short_id,
                "variance_amount": str(overage),
                "kind": "third_party_wo_variance_blocked",
            },
        )
    emails = [u.email for u in recipients if u.email]
    if emails:
        send_mail(
            f"[Variance Block] {wo.short_id}",
            message,
            None,
            emails,
            fail_silently=True,
        )


def _emit_closure_alert(wo: ThirdPartyWorkOrder) -> None:
    """Step 7: SIG closure alert email."""
    from django.core.mail import send_mail

    recipients = list(_sig_leader_and_finance_recipients(wo))
    emails = [u.email for u in recipients if u.email]
    if not emails:
        return
    subject = f"[WO Closed] {wo.short_id}"
    body = (
        f"{wo.title}\n"
        f"Vendor: {wo.vendor.name}\n"
        f"Final: ${wo.actual_invoice_total or '—'} (NTE ${wo.nte_amount or '—'})\n"
        f"Variance: {wo.variance_status or 'n/a'}\n"
    )
    send_mail(subject, body, None, emails, fail_silently=True)
