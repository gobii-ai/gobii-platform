from django.db import migrations


SWITCH_NAME = "homepage_perf_motion_reduction"


def add_switch(apps, schema_editor):
    """Enable the homepage performance rollback switch by default."""
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return

    Switch.objects.get_or_create(
        name=SWITCH_NAME,
        defaults={"active": True},
    )


def remove_switch(apps, schema_editor):
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return

    Switch.objects.filter(name=SWITCH_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0006_calltoaction_calltoactionversion"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switch, remove_switch),
    ]
