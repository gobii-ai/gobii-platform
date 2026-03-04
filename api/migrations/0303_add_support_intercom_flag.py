from django.db import migrations


FLAG_NAME = "support_intercom"


def add_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")

    if not Flag.objects.filter(name=FLAG_NAME).exists():
        Flag.objects.create(
            name=FLAG_NAME,
            everyone=None,
            percent=0,
            superusers=False,
            staff=False,
            authenticated=False,
        )


def noop(apps, schema_editor):
    """No reverse operation – keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0302_userpreference"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
