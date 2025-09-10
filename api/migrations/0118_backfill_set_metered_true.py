from django.db import migrations

def mark_existing_as_metered(apps, schema_editor):
    BrowserUseAgentTask = apps.get_model('api', 'BrowserUseAgentTask')
    PersistentAgentStep = apps.get_model('api', 'PersistentAgentStep')
    # Mark all existing rows as metered to avoid double billing on first rollup
    BrowserUseAgentTask.objects.filter(metered=False).update(metered=True)
    PersistentAgentStep.objects.filter(metered=False).update(metered=True)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0117_add_metered_flags"),
    ]

    operations = [
        migrations.RunPython(mark_existing_as_metered, migrations.RunPython.noop),
    ]

