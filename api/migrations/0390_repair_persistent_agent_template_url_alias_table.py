from django.db import migrations


def create_missing_url_alias_table(apps, schema_editor):
    Alias = apps.get_model("api", "PersistentAgentTemplateUrlAlias")
    table_name = Alias._meta.db_table
    existing_tables = schema_editor.connection.introspection.table_names()
    if table_name in existing_tables:
        return

    # Some local databases have 0385 recorded without the table it creates.
    schema_editor.create_model(Alias)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0389_browseruseagenttask_filespace_artifacts"),
    ]

    operations = [
        migrations.RunPython(create_missing_url_alias_table, migrations.RunPython.noop),
    ]
