from django.conf import settings
from django.db import migrations


FLAG_NAME = "contact_auto_approve_email"


def add_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    if Flag.objects.filter(name=FLAG_NAME).exists():
        return

    default_everyone = None if settings.GOBII_PROPRIETARY_MODE else True
    Flag.objects.create(
        name=FLAG_NAME,
        everyone=default_everyone,
        percent=0,
        superusers=False,
        staff=False,
        authenticated=False,
        note="Allow users to opt agents into automatically allowing new email contacts.",
    )


def noop(apps, schema_editor):
    """Keep the rollout flag when reversing this migration."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0425_persistentagent_contact_approval_mode"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
