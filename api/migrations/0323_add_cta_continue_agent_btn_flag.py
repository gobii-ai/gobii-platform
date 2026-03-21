from django.db import migrations


FLAG_NAME = "cta_continue_agent_btn"


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
        note='Show "Continue Your Agent" for pricing modal trial CTA buttons.',
    )


def noop(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0322_add_cta_pick_a_plan_flag"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
