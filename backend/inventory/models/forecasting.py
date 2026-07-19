"""Demand-forecast storage for non-serialized inventory items.

This module holds the *storage* model for ML-driven demand forecasting and
predictive reorder alerts. It is deliberately behaviour-free: rows are written
by the nightly forecasting task (a later bead) and read by the read-only
``demand_forecast`` / ``reorder_alerts`` API actions on
:class:`inventory.views.InventoryReportViewSet`.

One row is retained per item **per run** (history is kept); the API reads the
latest row per item. Every value the reorder decision needs is snapshotted at
generation time so later stock movements or price changes never rewrite a
historical row.

See :mod:`inventory.services.component_forecast` for the sibling *serialized*
forecast, whose row-payload style this mirrors.
"""

from __future__ import annotations

from django.db import models


class DemandForecast(models.Model):
    """A single forecast run's demand projection for one inventory item.

    Populated by the forecasting task (op-2); read by the API. The ``method``
    records how the projection was produced -- a seasonal Holt-Winters smoother
    when enough history exists, otherwise a statistical run-rate fallback.
    ``PROPHET`` is retained for a future engine swap (see
    :mod:`inventory.services.demand_forecast_engine`).
    """

    class Method(models.TextChoices):
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
    horizon_days = models.PositiveIntegerField(
        help_text="Lead time + buffer used for the projection window.",
    )
    predicted_daily_demand = models.FloatField(
        help_text="Mean predicted demand per day (yhat).",
    )
    horizon_demand = models.FloatField(
        help_text="Sum of predicted demand (yhat) over the horizon.",
    )
    horizon_demand_upper = models.FloatField(
        help_text="Sum of the upper prediction band (yhat_upper) over the horizon; "
        "drives safety stock.",
    )
    available_at_generation = models.IntegerField(
        help_text="Snapshot of available/on-hand stock used for this projection.",
    )
    days_until_stockout = models.FloatField(null=True, blank=True)
    projected_stockout_date = models.DateField(null=True, blank=True)
    predictive_reorder_point = models.IntegerField()
    needs_reorder = models.BooleanField(
        help_text="available_at_generation <= predictive_reorder_point at generation time.",
    )
    lead_time_days = models.IntegerField(null=True, blank=True)
    safety_stock = models.IntegerField(default=0)
    method = models.CharField(max_length=20, choices=Method.choices)
    model_version = models.CharField(
        max_length=32,
        blank=True,
        help_text='Model identifier, e.g. "prophet-1".',
    )

    class Meta:
        ordering = ["-generated_at"]
        get_latest_by = "generated_at"
        indexes = [
            models.Index(fields=["item", "-generated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.item_id} @ {self.generated_at:%Y-%m-%d %H:%M} ({self.method})"
