from django.db import migrations, models


PREVIEW_FLAGS = (
    (
        "personal_agent_signup_starter_charter",
        (
            "Give proprietary personal no-plan users a built-in starter charter "
            "when immersive new-agent flow has no saved charter."
        ),
    ),
    (
        "personal_agent_signup_preview_ui",
        (
            "Show proprietary personal no-plan users the signup preview UI in "
            "immersive chat instead of the pricing modal / normal composer."
        ),
    ),
    (
        "personal_agent_signup_preview_processing_limit",
        (
            "Allow proprietary personal no-plan users to create a limited "
            "preview agent that pauses after its first reply until signup completes."
        ),
    ),
)


def add_flags(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    for flag_name, note in PREVIEW_FLAGS:
        if Flag.objects.filter(name=flag_name).exists():
            continue
        Flag.objects.create(
            name=flag_name,
            everyone=None,
            percent=0,
            superusers=False,
            staff=False,
            authenticated=False,
            note=note,
        )


def noop(apps, schema_editor):
    """No reverse operation; keep the flags if present."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0331_persistentmodelendpoint_allow_implied_send"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="signup_preview_state",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("awaiting_first_reply_pause", "Awaiting First Reply Pause"),
                    ("awaiting_signup_completion", "Awaiting Signup Completion"),
                ],
                default="none",
                help_text=(
                    "Personal proprietary signup-preview lifecycle state. "
                    "Used to pause limited preview agents until signup is completed."
                ),
                max_length=48,
            ),
        ),
        migrations.RunPython(add_flags, noop),
    ]
