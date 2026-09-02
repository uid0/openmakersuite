"""Recompute every ``LeadTimeLog``'s actual lead time in calendar days.

``actual_lead_time_days`` used to be an INCLUSIVE business-day count from the
order date while ``estimated_lead_time_days`` was — and still is — calendar
days taken from ``ItemSupplier.average_lead_time``. ``variance_days`` subtracts
one from the other, so it was subtracting two different units: every promise
kept inside the working week recorded ``+1`` and read as late, and only spans
crossing a weekend happened to cancel. ``supplier_selection``'s performance term
now reads that column to decide which vendor a purchase order goes to, so
leaving old rows in the old unit would mix two conventions across a single
supplier link's history.

Both values are recomputed from ``order_date`` and ``actual_delivery_date``
ALREADY STORED ON THE ROW, so this is an exact recomputation and not an
estimate.

``estimated_lead_time_days`` is deliberately NOT touched. The promise as of the
order date is not recoverable: the link's ``average_lead_time`` may have changed
since, and rows written before this branch recorded 14 for a same-day vendor
because of the ``average_lead_time or 14`` guard. Inventing a replacement would
be fabricating history, so the promise stays as it was recorded.

Reversible: the reverse operation puts the old inclusive business-day rule back,
so the change can be backed out.
"""

from datetime import timedelta

from django.db import migrations


def _as_date(value):
    return value.date() if hasattr(value, "date") else value


def _calendar_days(start, end):
    return max((end - start).days, 0)


def _inclusive_business_days(start, end):
    if start > end:
        return 0

    business_days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            business_days += 1
        current += timedelta(days=1)
    return business_days


def _recompute(apps, measure):
    LeadTimeLog = apps.get_model("reorder_queue", "LeadTimeLog")

    updated = []
    for log in LeadTimeLog.objects.all().iterator():
        actual = measure(_as_date(log.order_date), _as_date(log.actual_delivery_date))
        variance = actual - log.estimated_lead_time_days
        if actual == log.actual_lead_time_days and variance == log.variance_days:
            continue
        log.actual_lead_time_days = actual
        log.variance_days = variance
        updated.append(log)

    if updated:
        # ``bulk_update`` rather than ``save``: the historical model has no
        # ``save`` override, so variance would not be derived for us anyway, and
        # a whole-table rewrite through the ORM one row at a time is not worth
        # it on a column this migration already computes.
        LeadTimeLog.objects.bulk_update(
            updated, ["actual_lead_time_days", "variance_days"], batch_size=500
        )


def to_calendar_days(apps, schema_editor):
    _recompute(apps, _calendar_days)


def to_inclusive_business_days(apps, schema_editor):
    _recompute(apps, _inclusive_business_days)


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0034_leadtimelog_lead_times_in_calendar_days"),
    ]

    operations = [
        migrations.RunPython(to_calendar_days, to_inclusive_business_days),
    ]
