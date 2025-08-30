from django.db import migrations


class Migration(migrations.Migration):
    """Drop obsolete persistent-agent comms tables and purge them from Django state."""

    dependencies = [
        ("api", "0042_persistentagent_is_active"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # --- Database: force-drop tables if they exist ---
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        DROP TABLE IF EXISTS api_persistentagentemail CASCADE;
                        DROP TABLE IF EXISTS api_persistentagentsmsnumber CASCADE;
                        DROP TABLE IF EXISTS api_persistentagentmessage CASCADE;
                    """,
                    reverse_sql="""-- Irreversible -- tables removed intentionally.""",
                ),
            ],
            # --- State: forget the old models so Django no longer tracks them ---
            state_operations=[
                migrations.DeleteModel(name="PersistentAgentEmail"),
                migrations.DeleteModel(name="PersistentAgentSmsNumber"),
                migrations.DeleteModel(name="PersistentAgentMessage"),
            ],
        ),
    ] 