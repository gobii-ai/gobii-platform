from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0433_persistent_agent_emotion"),
    ]

    operations = [
        migrations.AlterField(
            model_name="persistentagenttoolcall",
            name="status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("queued", "Queued"),
                    ("pending", "Pending"),
                    ("complete", "Complete"),
                    ("error", "Error"),
                ],
                default="complete",
                help_text="Execution status for the tool call (queued, pending, complete, error).",
                max_length=32,
            ),
        ),
    ]
