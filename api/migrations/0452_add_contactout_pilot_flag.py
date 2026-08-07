from django.db import migrations


FLAG_NAME = "contactout_pilot"


def add_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    Flag.objects.get_or_create(
        name=FLAG_NAME,
        defaults={
            "everyone": None,
            "percent": 0,
            "superusers": False,
            "staff": False,
            "authenticated": False,
            "note": "Selected-user pilot for native ContactOut people and company sourcing.",
        },
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0451_disable_pipedream_google_sheets_guard_by_default"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
