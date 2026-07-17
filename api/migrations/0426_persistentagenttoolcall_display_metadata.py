from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0425_persistentagent_contact_approval_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttoolcall",
            name="display_metadata",
            field=models.JSONField(
                blank=True,
                help_text="Structured metadata used only to render this tool call in user-facing timelines.",
                null=True,
            ),
        ),
    ]
