from django.db import migrations


FLAG_NAME = "start_trial_capi_trial_eligibility_enforcement"


def add_flag(apps, schema_editor):
    try:
        Flag = apps.get_model("waffle", "Flag")
    except LookupError:
        return

    Flag.objects.update_or_create(
        name=FLAG_NAME,
        defaults={
            "everyone": False,
            "percent": 0,
            "superusers": False,
            "staff": False,
            "authenticated": False,
            "note": "Controls whether StartTrial CAPI is skipped when UserTrialEligibility is not eligible.",
        },
    )


def noop(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0335_globalsecret"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
