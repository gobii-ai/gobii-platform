from django.db import migrations


INDEX_NAME = "pa_msg_body_fts_gin"


def create_message_search_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
            ON api_persistentagentmessage
            USING GIN (to_tsvector('simple'::regconfig, COALESCE(body, '')))
            """,
        )


def drop_message_search_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("api", "0434_persistentagenttoolcall_queued_status"),
    ]

    operations = [
        migrations.RunPython(
            create_message_search_index,
            reverse_code=drop_message_search_index,
        ),
    ]
