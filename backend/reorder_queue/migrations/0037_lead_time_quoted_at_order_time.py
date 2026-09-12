"""Record the lead time a vendor quoted WHEN THE ORDER WAS SENT.

``PurchaseOrderItem.quoted_lead_time_days`` is the snapshot
``receiving.create_lead_time_log`` grades a delivery against. It had nothing to
read before this and took ``ItemSupplier.average_lead_time`` at RECEIPT time
instead — a live column that operators edit and
``inventory.tasks.update_average_lead_times`` rewrites on a schedule — so the
yardstick moved under finished orders and a kept promise could be recorded as
broken, or a broken one as kept.

**Existing rows are deliberately NOT backfilled.** The column is added NULL, and
NULL means "not recorded": the promise those orders were given is not held
anywhere. ``average_lead_time`` keeps no history, and the only order-time
derivation of it that survives — ``ReorderRequest.estimated_delivery`` — exists
only for lines that fulfilled an approved request, is overwritten by the order's
confirmed delivery date whenever one is set, and stays editable afterwards, so
it cannot be told apart from a value that has since been changed. Stamping
today's quote onto those lines would fabricate exactly the comparison this
migration exists to remove and make it permanent, which is the same refusal
``0035_backfill_lead_time_calendar_days`` made about ``estimated_lead_time_days``.

No stored ``LeadTimeLog`` changes. A line that is already fully received has its
row written and frozen, so the NULL reaches nothing but orders still in flight;
those fall back to the live quote — the number they were going to be graded
against anyway — and ``LeadTimeLog.estimated_lead_time_basis`` records that they
did. That column defaults to ``receipt_quote``, which is what every row written
before this migration actually holds, so the default states a fact rather than
guessing one.

Reversible: dropping both columns restores the previous shape. The judgement
already recorded on each ``LeadTimeLog`` survives the reverse, because this
migration rewrites none of them.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0036_leadtimelog_variance_yardstick_help_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadtimelog",
            name="estimated_lead_time_basis",
            field=models.CharField(
                choices=[
                    ("order_snapshot", "Quote when the order was sent"),
                    ("receipt_quote", "Quote at receipt (order predates the snapshot)"),
                ],
                default="receipt_quote",
                help_text=(
                    "Which reading of the supplier's quoted lead time "
                    "estimated_lead_time_days holds: the one captured when the order "
                    "was sent, or the link's quote at receipt for an order that "
                    "predates that capture."
                ),
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="purchaseorderitem",
            name="quoted_lead_time_days",
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    "Supplier's quoted lead time in calendar days, captured when this "
                    "order was sent. NULL on lines with no supplier link, on unsent "
                    "orders, and on orders sent before this was recorded."
                ),
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="leadtimelog",
            name="variance_days",
            field=models.IntegerField(
                help_text=(
                    "Actual minus estimated lead time, in calendar days (positive = "
                    "later than quoted). Measured against the supplier's quoted lead "
                    "time, NOT against expected_delivery_date, which is the order's "
                    "separately confirmed date and a different fact."
                )
            ),
        ),
    ]
