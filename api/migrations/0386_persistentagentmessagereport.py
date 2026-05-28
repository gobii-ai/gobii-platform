import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0385_unique_public_template_slugs"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentMessageReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("comment", models.TextField(blank=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("submitted", "Submitted"),
                            ("judge_completed", "Judge Completed"),
                            ("judge_failed", "Judge Failed"),
                        ],
                        db_index=True,
                        default="submitted",
                        max_length=32,
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="message_reports",
                        to="api.persistentagent",
                    ),
                ),
                (
                    "judge_completion",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="message_reports",
                        to="api.persistentagentcompletion",
                    ),
                ),
                (
                    "judge_suggestion",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="message_reports",
                        to="api.persistentagentjudgesuggestion",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reports",
                        to="api.persistentagentmessage",
                    ),
                ),
                (
                    "reporter",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="persistent_agent_message_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["agent", "-created_at"], name="pa_msg_report_agent_idx"),
                    models.Index(fields=["message", "-created_at"], name="pa_msg_report_msg_idx"),
                    models.Index(fields=["reporter", "-created_at"], name="pa_msg_report_user_idx"),
                    models.Index(fields=["status", "-created_at"], name="pa_msg_report_status_idx"),
                ],
            },
        ),
    ]
