from django.db import migrations


SWITCH_NAME = "pipedream_google_sheets_guard"


def disable_switch(apps, schema_editor):
    Switch = apps.get_model("waffle", "Switch")
    # Preview databases may have applied the original 0450 while its default was active.
    Switch.objects.update_or_create(
        name=SWITCH_NAME,
        defaults={"active": False},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0450_add_pipedream_google_sheets_guard_switch"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(disable_switch, migrations.RunPython.noop),
    ]
