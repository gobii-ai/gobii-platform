from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0205_merge_20251125_1840"),
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
                    ("tool_search", "Tool Search"),
                    ("other", "Other"),
                ],
                default="orchestrator",
                help_text="Origin of the completion (orchestrator loop, compaction, tag generation, etc.).",
                max_length=64,
            ),
            preserve_default=True,
        ),
    ]
