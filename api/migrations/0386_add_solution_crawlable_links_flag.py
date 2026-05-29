from django.db import migrations


FLAG_NAME = "solution_crawlable_links"


def add_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    if Flag.objects.filter(name=FLAG_NAME).exists():
        return
    Flag.objects.create(
        name=FLAG_NAME,
        everyone=True,
        percent=0,
        superusers=False,
        staff=False,
        authenticated=False,
        note="Show crawlable related links on dedicated /solutions/* marketing pages.",
    )


def noop(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0385_unique_public_template_slugs"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flag, noop),
    ]
