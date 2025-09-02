from django.db import migrations


def add_switch(apps, schema_editor):
    """Create the waffle switch for agent soft expiration (idempotent)."""
    try:
        Switch = apps.get_model('waffle', 'Switch')
    except LookupError:
        # Waffle not installed in this environment
        return
    Switch.objects.update_or_create(
        name='agent_soft_expiration',
        defaults={'active': False},
    )


def remove_switch(apps, schema_editor):
    try:
        Switch = apps.get_model('waffle', 'Switch')
    except LookupError:
        return
    Switch.objects.filter(name='agent_soft_expiration').delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0107_persistentagent_last_expired_at_and_more"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switch, remove_switch),
    ]

