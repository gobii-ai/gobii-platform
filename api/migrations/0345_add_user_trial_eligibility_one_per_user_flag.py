from django.db import migrations


FLAG_NAME = "user_trial_eligibility_enforcement_one_per_user"


def add_flag(apps, schema_editor):
    try:
        Flag = apps.get_model("waffle", "Flag")
    except LookupError:
        return

    Flag.objects.update_or_create(
        name=FLAG_NAME,
        defaults={
            "everyone": None,
            "percent": 0,
            "superusers": False,
            "staff": False,
            "authenticated": False,
            "note": "Controls whether personal trial eligibility only checks a user's own prior billing or trial history.",
        },
    )


def noop(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0344_add_add_payment_info_sent_at_to_stripe_checkout_context"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
