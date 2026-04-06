from django.db import migrations


FLAG_NAME = "cta_unlock_agent_copy"


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
        note=(
            "Use the bundled 'unlock your agent' CTA copy on immersive signup preview and the pricing page."
        ),
    )


def noop(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0333_globalagentskill_and_agentskill_source"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
