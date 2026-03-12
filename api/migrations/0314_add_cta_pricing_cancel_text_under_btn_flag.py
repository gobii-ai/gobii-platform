from django.db import migrations


FLAG_NAME = "cta_pricing_cancel_text_under_btn"


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
            "Show cancellation reassurance beneath pricing trial CTA buttons when the CTA is a trial action."
        ),
    )


def noop(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0313_add_owner_execution_pause_switches"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
