from django.db import migrations


SWITCH_NAME = "pipedream_google_sheets_guard"


def add_switch(apps, schema_editor):
    Switch = apps.get_model("waffle", "Switch")
    Switch.objects.get_or_create(
        name=SWITCH_NAME,
        defaults={"active": False},
    )


def remove_switch(apps, schema_editor):
    Switch = apps.get_model("waffle", "Switch")
    Switch.objects.filter(name=SWITCH_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0449_visible_agent_message_roster_index"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_switch, remove_switch),
    ]
