"""Make a supplier link's ``(item, supplier)`` uniqueness deferrable.

Replaces the ``unique_together`` index with the same pair as a
``UNIQUE ... DEFERRABLE INITIALLY IMMEDIATE`` constraint. Every existing write is
still checked at the end of its own statement, so nothing a current client does
changes; the difference is that one transaction may say ``SET CONSTRAINTS ...
DEFERRED`` and have the pairs judged once its whole change is applied, which is
how two links exchange suppliers (``inventory.services.link_batch``).

No row can violate the new constraint: the old one held the same pair unique up
to this migration, and both operations run in one transaction on PostgreSQL.
"""

import django.db.models.constraints
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0115_itemsupplier_version"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="itemsupplier",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="itemsupplier",
            constraint=models.UniqueConstraint(
                deferrable=django.db.models.constraints.Deferrable["IMMEDIATE"],
                fields=("item", "supplier"),
                name="inventory_itemsupplier_item_supplier_uniq",
            ),
        ),
    ]
