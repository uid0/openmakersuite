from django.db import migrations

FORWARD_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'reorder_queue_purchaseorderitem'
          AND column_name = 'order_in_packages'
          AND data_type = 'boolean'
    ) THEN
        ALTER TABLE reorder_queue_purchaseorderitem
            ALTER COLUMN order_in_packages DROP DEFAULT;
        ALTER TABLE reorder_queue_purchaseorderitem
            ALTER COLUMN order_in_packages TYPE integer
            USING CASE WHEN order_in_packages THEN 1 ELSE 0 END;
        ALTER TABLE reorder_queue_purchaseorderitem
            ALTER COLUMN order_in_packages SET DEFAULT 0;
        ALTER TABLE reorder_queue_purchaseorderitem
            ADD CONSTRAINT reorder_queue_purchaseorderitem_order_in_packages_check
            CHECK (order_in_packages >= 0);
    END IF;
END $$;
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(FORWARD_SQL)


def reverse(apps, schema_editor):
    # No-op: boolean was the broken state; we don't restore it.
    return


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0015_fix_constraint_syntax_for_freeform"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
