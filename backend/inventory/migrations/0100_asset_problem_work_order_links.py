"""Link an AssetProblem to the work order it was promoted to.

Both FKs are nullable and ``SET_NULL``: a report exists before any work order
does, and deleting the work order must not delete the report of the problem.
Mirrors the pair ``LocationProblem`` has carried since the location-problem
flow shipped.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0099_work_order_nullable_item_direct_asset'),
        ('maintenance_orders', '0005_thirdpartyworkorder_location'),
    ]

    operations = [
        migrations.AddField(
            model_name='assetproblem',
            name='third_party_work_order',
            field=models.ForeignKey(blank=True, help_text='Third-party work order this problem was promoted to', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='asset_problems', to='maintenance_orders.thirdpartyworkorder'),
        ),
        migrations.AddField(
            model_name='assetproblem',
            name='work_order',
            field=models.ForeignKey(blank=True, help_text='In-house corrective work order this problem was promoted to', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='asset_problems', to='inventory.workorder'),
        ),
    ]
