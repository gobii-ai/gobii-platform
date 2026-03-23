from django.db import migrations


FLAG_NAME = "user_trial_eligibility_enforcement"


def add_flag(apps, schema_editor):
    try:
        Flag = apps.get_model("waffle", "Flag")
    except LookupError:
        return

    Flag.objects.update_or_create(
        name=FLAG_NAME,
        defaults={
            "everyone": True,
            "percent": 0,
            "superusers": False,
            "staff": False,
            "authenticated": False,
            "note": "Controls whether UserTrialEligibility enforcement blocks trial CTAs and checkout trial periods.",
        },
    )


def noop(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0323_backfill_user_identity_signals_from_attribution"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
