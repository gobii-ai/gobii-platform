from django.db import migrations


FLAGS = {
    "start_trial_capi_send_review": (
        "Controls whether StartTrial CAPI is still sent for stored review decisions "
        "when the StartTrial CAPI trial-eligibility policy is enabled."
    ),
    "start_trial_capi_send_no_trial": (
        "Controls whether StartTrial CAPI is still sent for stored no-trial decisions "
        "when the StartTrial CAPI trial-eligibility policy is enabled."
    ),
}


def add_flags(apps, schema_editor):
    try:
        Flag = apps.get_model("waffle", "Flag")
    except LookupError:
        return

    for flag_name, note in FLAGS.items():
        Flag.objects.update_or_create(
            name=flag_name,
            defaults={
                "everyone": False,
                "percent": 0,
                "superusers": False,
                "staff": False,
                "authenticated": False,
                "note": note,
            },
        )


def noop(apps, schema_editor):
    """No reverse operation; keep the flags if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0337_merge_20260407_1834"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_flags, noop),
    ]
