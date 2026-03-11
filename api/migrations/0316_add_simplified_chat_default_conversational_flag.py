from django.db import migrations


FLAG_NAME = "simplified_chat_default_conversational"


def add_flag(apps, schema_editor):
    """Create the simplified chat default-view waffle flag (idempotent)."""
    try:
        Flag = apps.get_model("waffle", "Flag")
    except LookupError:
        return
    Flag.objects.update_or_create(
        name=FLAG_NAME,
        defaults={"superusers": True},
    )


def remove_flag(apps, schema_editor):
    try:
        Flag = apps.get_model("waffle", "Flag")
    except LookupError:
        return
    Flag.objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0315_merge_20260311_2033"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, remove_flag),
    ]
