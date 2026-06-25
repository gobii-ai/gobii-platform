from django.db import migrations


SWITCH_NAME = "agent_retry_completion_on_web_session_activation"


def remove_switch(apps, schema_editor):
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return
    Switch.objects.filter(name=SWITCH_NAME).delete()


def restore_switch(apps, schema_editor):
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return
    Switch.objects.update_or_create(
        name=SWITCH_NAME,
        defaults={"active": False},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0401_merge_20260618_1848"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(remove_switch, restore_switch),
    ]
