# Generated manually to ensure auth_user_groups table exists for custom User model

from django.db import migrations


def create_auth_user_groups_table(apps, schema_editor):
    """Create auth_user_groups table if it doesn't exist."""
    vendor = schema_editor.connection.vendor
    connection = schema_editor.connection

    # Check if table exists
    with connection.cursor() as cursor:
        if vendor == "postgresql":
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'auth_user_groups'
                );
                """
            )
            exists = cursor.fetchone()[0]
        elif vendor == "sqlite":
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_user_groups';"
            )
            exists = cursor.fetchone() is not None
        else:
            # For other databases, assume table exists
            exists = True

        if not exists:
            # Create the table using database-specific SQL
            if vendor == "postgresql":
                cursor.execute(
                    """
                    CREATE TABLE auth_user_groups (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        group_id INTEGER NOT NULL,
                        UNIQUE(user_id, group_id),
                        FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE,
                        FOREIGN KEY (group_id) REFERENCES auth_group(id) ON DELETE CASCADE
                    );
                    CREATE INDEX auth_user_groups_user_id_idx ON auth_user_groups(user_id);
                    CREATE INDEX auth_user_groups_group_id_idx ON auth_user_groups(group_id);
                    """
                )
            elif vendor == "sqlite":
                # SQLite requires separate execute calls for each statement
                cursor.execute(
                    """
                    CREATE TABLE auth_user_groups (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        group_id INTEGER NOT NULL,
                        UNIQUE(user_id, group_id),
                        FOREIGN KEY (user_id) REFERENCES auth_user(id),
                        FOREIGN KEY (group_id) REFERENCES auth_group(id)
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX auth_user_groups_user_id_idx ON auth_user_groups(user_id)"
                )
                cursor.execute(
                    "CREATE INDEX auth_user_groups_group_id_idx ON auth_user_groups(group_id)"
                )


def reverse_create_auth_user_groups_table(apps, schema_editor):
    """Drop auth_user_groups table if it exists."""
    vendor = schema_editor.connection.vendor
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        if vendor == "postgresql":
            cursor.execute("DROP TABLE IF EXISTS auth_user_groups CASCADE;")
        elif vendor == "sqlite":
            cursor.execute("DROP TABLE IF EXISTS auth_user_groups;")


class Migration(migrations.Migration):

    dependencies = [
        ("membership", "0003_sigadmin"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(
            code=create_auth_user_groups_table,
            reverse_code=reverse_create_auth_user_groups_table,
        ),
    ]
