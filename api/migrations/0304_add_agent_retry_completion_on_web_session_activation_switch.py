from django.db import migrations


SWITCH_NAME = "agent_retry_completion_on_web_session_activation"


def add_switch(apps, schema_editor):
    """Create the retry-on-web-session-activation switch (idempotent)."""
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return
    Switch.objects.update_or_create(
        name=SWITCH_NAME,
        defaults={"active": False},
    )


def remove_switch(apps, schema_editor):
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return
    Switch.objects.filter(name=SWITCH_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0303_add_support_intercom_flag"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switch, remove_switch),
    ]

