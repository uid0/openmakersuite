"""Demand-forecast storage for non-serialized inventory items.

This module holds the *storage* model for demand forecasting and predictive
reorder alerts. It is deliberately behaviour-free: rows are written by the
nightly forecasting task and read by the read-only ``demand_forecast`` /
``reorder_alerts`` API actions on
:class:`inventory.views.InventoryReportViewSet`.

One row is retained per item **per run** (history is kept); the API reads the
latest row per item. Every value the reorder decision needs is snapshotted at
generation time so later stock movements or price changes never rewrite a
historical row.

Two generations of fields live here (v2 replaced the model, not the table):

* **interval fields** (``avg_interval_days`` … ``days_until_due``) — the live
  v2 *restock-interval* signal: how often this item actually gets bought, and
  therefore when it is due to be bought again.
* **quantity fields** (``predicted_daily_demand`` … ``projected_stockout_date``)
  — the retired v1 usage-rate projection. Retained so historical rows stay
  readable and so the existing API/web/scantty consumers keep their keys;
  v2 rows write them as ``0``/``NULL``.

See :mod:`inventory.services.component_forecast` for the sibling *serialized*
forecast, whose row-payload style this mirrors.
"""

from __future__ import annotations

from django.db import models


class DemandForecast(models.Model):
    """A single forecast run's restock projection for one inventory item.

    Populated by the forecasting task; read by the API. The ``method`` records
    how the projection was produced -- ``RESTOCK_INTERVAL`` when the item has
    enough purchase history to average a cadence from, otherwise
    ``INSUFFICIENT_HISTORY`` (see
    :mod:`inventory.services.demand_forecast_engine`).

    The ``PROPHET`` / ``HOLTWINTERS`` / ``FALLBACK`` members are the retired v1
    usage-rate methods, kept so pre-v2 rows still resolve a display label.
    """

    class Method(models.TextChoices):
        RESTOCK_INTERVAL = "restock_interval", "Restock interval"
        INSUFFICIENT_HISTORY = "insufficient_history", "Insufficient history"
        # Retired v1 (usage-rate) methods -- historical rows only.
        PROPHET = "prophet", "Prophet"
        HOLTWINTERS = "holtwinters", "Holt-Winters seasonal"
        FALLBACK = "fallback", "Statistical fallback"

    item = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.CASCADE,
        related_name="demand_forecasts",
    )
    generated_at = models.DateTimeField(
        db_index=True,
        help_text="Timestamp of the forecast run that produced this row.",
    )

    # --- restock-interval signal (v2) --------------------------------------
    avg_interval_days = models.FloatField(
        null=True,
        blank=True,
        help_text="Mean number of days between this item's purchase events; "
        "NULL when there is not enough history to average one.",
    )
    interval_samples = models.IntegerField(
        default=0,
        help_text="Number of gaps the average was taken over (purchase events - 1).",
    )
    last_restock_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of the most recent purchase event for this item.",
    )
    predicted_next_reorder_date = models.DateField(
        null=True,
        blank=True,
        help_text="last_restock_date + avg_interval_days -- when the item is due to be bought again.",
    )
    days_until_due = models.FloatField(
        null=True,
        blank=True,
        help_text="Days from generation to predicted_next_reorder_date; negative when overdue.",
    )

    # --- retired v1 quantity projection: 0/NULL on v2 rows -----------------
    horizon_days = models.PositiveIntegerField(
        help_text="Retired (v1): lead time + buffer used for the projection window.",
    )
    predicted_daily_demand = models.FloatField(
        help_text="Retired (v1): mean predicted demand per day (yhat).",
    )
    horizon_demand = models.FloatField(
        help_text="Retired (v1): sum of predicted demand (yhat) over the horizon.",
    )
    horizon_demand_upper = models.FloatField(
        help_text="Retired (v1): sum of the upper prediction band (yhat_upper) over the horizon.",
    )
    days_until_stockout = models.FloatField(
        null=True,
        blank=True,
        help_text="Retired (v1): available / predicted daily demand.",
    )
    projected_stockout_date = models.DateField(
        null=True,
        blank=True,
        help_text="Retired (v1): date the v1 projection ran the item to zero.",
    )
    predictive_reorder_point = models.IntegerField(
        help_text="Retired (v1): stock level the v1 projection reordered at.",
    )
    safety_stock = models.IntegerField(
        default=0,
        help_text="Retired (v1): buffer built from the v1 prediction band.",
    )

    # --- decision + provenance (both generations) --------------------------
    available_at_generation = models.IntegerField(
        help_text="Snapshot of available/on-hand stock at generation. Informational "
        "under the interval model -- it does not drive needs_reorder.",
    )
    needs_reorder = models.BooleanField(
        help_text="Item is due to be bought: days_until_due <= lead_time_days at "
        "generation time.",
    )
    lead_time_days = models.IntegerField(null=True, blank=True)
    method = models.CharField(max_length=32, choices=Method.choices)
    model_version = models.CharField(
        max_length=32,
        blank=True,
        help_text='Model identifier, e.g. "interval-1".',
    )

    class Meta:
        ordering = ["-generated_at"]
        get_latest_by = "generated_at"
        indexes = [
            models.Index(fields=["item", "-generated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.item_id} @ {self.generated_at:%Y-%m-%d %H:%M} ({self.method})"
