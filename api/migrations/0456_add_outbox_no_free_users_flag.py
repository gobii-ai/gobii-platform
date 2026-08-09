from django.db import migrations


FLAG_NAME = "outbox_no_free_users"


def add_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    if Flag.objects.filter(name=FLAG_NAME).exists():
        return

    Flag.objects.create(
        name=FLAG_NAME,
        everyone=None,
        percent=0,
        superusers=False,
        staff=False,
        authenticated=False,
        note="Restrict Review Before Send UI controls to paid plans.",
    )


def noop(apps, schema_editor):
    """Keep the rollout flag when reversing this migration."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0455_user_discord_identity"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
