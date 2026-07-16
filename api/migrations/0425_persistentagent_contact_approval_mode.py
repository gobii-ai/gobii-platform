from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0424_persistentagentmessagefeedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="contact_approval_mode",
            field=models.CharField(
                choices=[
                    ("require_approval", "Require approval"),
                    ("auto_approve_email", "Automatically allow email contacts"),
                ],
                default="require_approval",
                help_text=(
                    "Controls whether new email recipients require per-contact approval or are "
                    "automatically added to the agent's contact list. SMS always requires approval."
                ),
                max_length=32,
            ),
        ),
    ]
