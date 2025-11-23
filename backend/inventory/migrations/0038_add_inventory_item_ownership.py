# Generated manually for SIG management system

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0037_itemsupplier_is_discontinued"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("membership", "0003_sigadmin"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryitem",
            name="ownership_type",
            field=models.CharField(
                choices=[("user", "User"), ("group", "Group"), ("space", "Space")],
                default="space",
                help_text="Type of ownership for this inventory item",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="owning_group",
            field=models.ForeignKey(
                blank=True,
                help_text="Group (SIG) that owns this inventory item (if applicable)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_inventory_items",
                to="auth.group",
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="owning_user",
            field=models.ForeignKey(
                blank=True,
                help_text="User that owns this inventory item (if applicable)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_inventory_items",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
