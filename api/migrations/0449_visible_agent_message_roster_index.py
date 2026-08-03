from django.db import migrations


def create_roster_latest_index(_apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS agent_message_roster_latest_idx
            ON api_persistentagentmessage
                (owner_agent_id, timestamp DESC, seq DESC)
            INCLUDE (id, conversation_id)
            WHERE is_outbound = TRUE
              AND peer_agent_id IS NULL
              AND (
                  raw_payload -> 'hide_in_chat' IS NULL
                  OR raw_payload -> 'hide_in_chat' = 'false'::jsonb
              )
              AND (raw_payload ->> 'source_kind') IS DISTINCT FROM 'mcp'
        """)


def drop_roster_latest_index(_apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP INDEX CONCURRENTLY IF EXISTS agent_message_roster_latest_idx")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("api", "0448_freeze_approved_email_content"),
    ]

    operations = [
        migrations.RunPython(
            create_roster_latest_index,
            reverse_code=drop_roster_latest_index,
        ),
    ]
