from django.db import migrations

FLAG_NAME = "simplified_chat_ui"


def add_flag(apps, schema_editor):
    """Create the simplified_chat_ui waffle flag (idempotent)."""
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
        ("api", "0310_imagegenerationllmtier_use_case"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, remove_flag),
    ]
