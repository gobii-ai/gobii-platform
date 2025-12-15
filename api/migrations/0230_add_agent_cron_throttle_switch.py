from django.db import migrations


def add_switch(apps, schema_editor):
    """Create the waffle switch for free-plan cron throttling (idempotent)."""
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return
    Switch.objects.update_or_create(
        name="agent_cron_throttle",
        defaults={"active": False},
    )


def remove_switch(apps, schema_editor):
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return
    Switch.objects.filter(name="agent_cron_throttle").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0229_browserconfig_vision_detail_level"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switch, remove_switch),
    ]

