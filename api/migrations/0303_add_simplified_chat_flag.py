from django.db import migrations


FLAG_NAME = "simplified_agent_chat_ui"


def add_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")

    if Flag.objects.filter(name=FLAG_NAME).exists():
        return

    Flag.objects.create(
        name=FLAG_NAME,
        everyone=None,
        percent=0,
        superusers=True,
        staff=False,
        authenticated=False,
    )


def noop(apps, schema_editor):
    """No reverse operation; preserve flags if they already exist."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0302_userpreference"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
