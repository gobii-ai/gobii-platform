from django.db import migrations


SCALE_TRIAL_CHECKOUT_BILLING_ADDRESS_REQUIRED = (
    "stripe_scale_trial_checkout_billing_address_required"
)
SCALE_TRIAL_CHECKOUT_INDIVIDUAL_NAME_ENABLED = (
    "stripe_scale_trial_checkout_individual_name_enabled"
)
SCALE_TRIAL_CHECKOUT_INDIVIDUAL_NAME_OPTIONAL = (
    "stripe_scale_trial_checkout_individual_name_optional"
)


def add_switches(apps, schema_editor):
    """Create disabled Scale trial checkout switches so rollout is explicit."""
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return

    for switch_name in (
        SCALE_TRIAL_CHECKOUT_BILLING_ADDRESS_REQUIRED,
        SCALE_TRIAL_CHECKOUT_INDIVIDUAL_NAME_ENABLED,
        SCALE_TRIAL_CHECKOUT_INDIVIDUAL_NAME_OPTIONAL,
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
            SCALE_TRIAL_CHECKOUT_BILLING_ADDRESS_REQUIRED,
            SCALE_TRIAL_CHECKOUT_INDIVIDUAL_NAME_ENABLED,
            SCALE_TRIAL_CHECKOUT_INDIVIDUAL_NAME_OPTIONAL,
        ]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0359_add_persistent_agent_planning_mode_flag"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switches, remove_switches),
    ]
