from django.db import migrations, models


def _populate_completion_type(apps, schema_editor):
    Completion = apps.get_model("api", "PersistentAgentCompletion")
    Completion.objects.filter(completion_type__isnull=True).update(completion_type="orchestrator")


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0211_eval_run_primary_model"),
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
                help_text="Origin of the completion (orchestrator loop, compaction, tag generation, etc.).",
                max_length=64,
                null=True,
            ),
        ),
        migrations.RunPython(_populate_completion_type, migrations.RunPython.noop),
        migrations.AlterField(
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
