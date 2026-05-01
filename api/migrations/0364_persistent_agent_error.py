import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0363_merge_20260429_2023"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentError",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("LLM_COMPLETION", "LLM Completion"),
                            ("TASK_QUOTA_EXCEEDED", "Task Quota Exceeded"),
                            ("PROMPT_CONSTRUCTION", "Prompt Construction"),
                            ("TOOL_PERSISTENCE", "Tool Persistence"),
                            ("CREDIT_FAILURE", "Credit Failure"),
                            ("OTHER", "Other"),
                        ],
                        default="OTHER",
                        help_text="Broad error category for filtering and audit display.",
                        max_length=64,
                    ),
                ),
                ("source", models.CharField(help_text="Code path that recorded this error.", max_length=256)),
                ("level", models.CharField(default="ERROR", help_text="Server log level used for this error.", max_length=16)),
                ("message", models.TextField(blank=True)),
                ("exception_class", models.CharField(blank=True, max_length=256)),
                ("traceback", models.TextField(blank=True)),
                ("context", models.JSONField(blank=True, default=dict)),
                (
                    "agent",
                    models.ForeignKey(
                        help_text="Persistent agent this error belongs to.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="errors",
                        to="api.persistentagent",
                    ),
                ),
                (
                    "completion",
                    models.ForeignKey(
                        blank=True,
                        help_text="Completion associated with this error, when available.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="errors",
                        to="api.persistentagentcompletion",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="persistentagenterror",
            index=models.Index(fields=["agent", "-created_at"], name="pa_error_recent_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagenterror",
            index=models.Index(fields=["category"], name="pa_error_category_idx"),
        ),
    ]
