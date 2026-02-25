from django.db import migrations


SWITCH_NAME = "fish_collateral"


def add_switch(apps, schema_editor):
    """Create the rollout switch for fish collateral (idempotent)."""
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return
    Switch.objects.update_or_create(
        name=SWITCH_NAME,
        defaults={"active": False},
    )


def remove_switch(apps, schema_editor):
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return
    Switch.objects.filter(name=SWITCH_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0294_add_fish_flags"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switch, remove_switch),
    ]
