from django.db import migrations


FLAG_NAME = "persistent_agent_planning_mode"


def add_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    if Flag.objects.filter(name=FLAG_NAME).exists():
        return
    Flag.objects.create(
        name=FLAG_NAME,
        everyone=True,
        percent=0,
        superusers=False,
        staff=False,
        authenticated=False,
        note="Enable prompt-led planning mode by default for newly provisioned persistent agents.",
    )


def noop(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0358_merge_20260422_1156"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
