from django.db import migrations


FLAG_NAME = "user_trial_review_allows_trial"


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
            "note": "Controls whether review trial eligibility decisions are treated as trial-allowed while explicit no-trial decisions remain blocked.",
        },
    )


def noop(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0334_add_cta_unlock_agent_copy_flag"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
