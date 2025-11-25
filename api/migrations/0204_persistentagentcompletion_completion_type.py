from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0203_promptconfig_max_unified_history_hysteresis_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentcompletion",
            name="completion_type",
            field=models.CharField(
                choices=[
                    ("orchestrator", "Orchestrator"),
                    ("compaction", "Comms Compaction"),
                    ("step_compaction", "Step Compaction"),
                    ("tag", "Tag Generation"),
                    ("short_description", "Short Description"),
                    ("mini_description", "Mini Description"),
                    ("other", "Other"),
                ],
                default="orchestrator",
                help_text="Origin of the completion (orchestrator loop, compaction, tag generation, etc.).",
                max_length=64,
            ),
            preserve_default=True,
        ),
    ]
