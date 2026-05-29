from django.db import migrations


SWITCH_NAME = "stripe_checkout_tos_consent_required"


def add_switch(apps, schema_editor):
    """Require TOS consent by default while preserving any pre-created state."""
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
        ("api", "0386_add_solution_crawlable_links_flag"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switch, remove_switch),
    ]
