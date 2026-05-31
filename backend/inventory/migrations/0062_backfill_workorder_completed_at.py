"""Backfill WorkOrder.completed_at for already-completed work orders.

Digital completions never stamped ``completed_at`` (it is read-only over the
API and the viewset did not set it), so completed work orders created that way
rendered as "Completed N/A" in the asset detail view. Going forward the viewset
stamps the timestamp; this migration repairs the existing rows using
``updated_at`` as the best available proxy for when the status last changed.
"""

from django.db import migrations
from django.db.models import F


def backfill_completed_at(apps, schema_editor):
    WorkOrder = apps.get_model("inventory", "WorkOrder")
    WorkOrder.objects.filter(
        status="completed",
        completed_at__isnull=True,
    ).update(completed_at=F("updated_at"))


def noop_reverse(apps, schema_editor):
    # Irreversible data repair; leave the backfilled timestamps in place.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0061_remove_asset_access_code_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_completed_at, noop_reverse),
    ]
