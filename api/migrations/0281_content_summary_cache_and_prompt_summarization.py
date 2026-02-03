import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0280_summarization_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContentSummaryCache",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("content_hash", models.CharField(max_length=64, db_index=True)),
                ("summary_type", models.CharField(max_length=64, db_index=True)),
                ("summary", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["summary_type", "content_hash"], name="content_summary_type_hash_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=["content_hash", "summary_type"],
                        name="content_summary_cache_unique",
                    ),
                ],
            },
        ),
        migrations.AlterField(
            model_name="persistentagentcompletion",
            name="completion_type",
            field=models.CharField(
                choices=[
                    ("orchestrator", "Orchestrator"),
                    ("compaction", "Comms Compaction"),
                    ("step_compaction", "Step Compaction"),
                    ("prompt_summarization", "Prompt Summarization"),
                    ("tag", "Tag Generation"),
                    ("short_description", "Short Description"),
                    ("mini_description", "Mini Description"),
                    ("tool_search", "Tool Search"),
                    ("template_clone", "Template Clone"),
                    ("other", "Other"),
                ],
                default="orchestrator",
                help_text="Origin of the completion (orchestrator loop, compaction, tag generation, etc.).",
                max_length=64,
            ),
        ),
    ]
