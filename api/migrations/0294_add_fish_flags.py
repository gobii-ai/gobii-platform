from django.db import migrations


FLAGS = (
    ("fish_upper_left", True),
    ("fish_homepage", True),
)


def add_flags(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")

    for flag_name, everyone in FLAGS:
        if Flag.objects.filter(name=flag_name).exists():
            continue

        Flag.objects.create(
            name=flag_name,
            everyone=everyone,
            percent=0,
            superusers=False,
            staff=False,
            authenticated=False,
        )


def noop(apps, schema_editor):
    """No reverse operation; keep flags if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0293_persistentagentskill"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flags, noop),
    ]
