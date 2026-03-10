from django.db import migrations


BILLING_DELINQUENCY_SWITCH = "owner_execution_pause_on_billing_delinquency"
TRIAL_CONVERSION_FAILED_SWITCH = "owner_execution_pause_on_trial_conversion_failed"


def add_switches(apps, schema_editor):
    """Create owner execution pause switches in the disabled state (idempotent)."""
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return

    for switch_name in (
        BILLING_DELINQUENCY_SWITCH,
        TRIAL_CONVERSION_FAILED_SWITCH,
    ):
        Switch.objects.update_or_create(
            name=switch_name,
            defaults={"active": False},
        )


def remove_switches(apps, schema_editor):
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return

    Switch.objects.filter(
        name__in=[
            BILLING_DELINQUENCY_SWITCH,
            TRIAL_CONVERSION_FAILED_SWITCH,
        ]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0312_execution_pause_reason_choices"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switches, remove_switches),
    ]
