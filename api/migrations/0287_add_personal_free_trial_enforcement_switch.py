from django.db import migrations


SWITCH_NAME = "personal_free_trial_enforcement"


def add_switch(apps, schema_editor):
    """Create the rollout switch for personal free-trial enforcement."""
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
        ("api", "0286_userflags_is_freemium_grandfathered"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switch, remove_switch),
    ]
