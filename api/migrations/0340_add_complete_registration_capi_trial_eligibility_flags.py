from django.db import migrations


FLAGS = {
    "complete_registration_capi_trial_eligibility_enforcement": (
        "Controls whether CompleteRegistration CAPI is skipped when "
        "UserTrialEligibility is not eligible."
    ),
    "complete_registration_capi_send_review": (
        "Controls whether CompleteRegistration CAPI is still sent for stored "
        "review decisions when the CompleteRegistration CAPI trial-eligibility "
        "policy is enabled."
    ),
    "complete_registration_capi_send_no_trial": (
        "Controls whether CompleteRegistration CAPI is still sent for stored "
        "no-trial decisions when the CompleteRegistration CAPI trial-eligibility "
        "policy is enabled."
    ),
}


def add_flags(apps, schema_editor):
    try:
        Flag = apps.get_model("waffle", "Flag")
    except LookupError:
        return

    for flag_name, note in FLAGS.items():
        Flag.objects.update_or_create(
            name=flag_name,
            defaults={
                "everyone": False,
                "percent": 0,
                "superusers": False,
                "staff": False,
                "authenticated": False,
                "note": note,
            },
        )


def noop(apps, schema_editor):
    """No reverse operation; keep the flags if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0339_add_add_payment_info_capi_trial_eligibility_flags"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flags, noop),
    ]
