import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0385_unique_public_template_slugs"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentWorkPlan",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("completed", "Completed"),
                            ("superseded", "Superseded"),
                        ],
                        default="active",
                        max_length=16,
                    ),
                ),
                ("title", models.CharField(blank=True, max_length=255)),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("superseded_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="work_plans",
                        to="api.persistentagent",
                    ),
                ),
            ],
            options={
                "ordering": ["-started_at"],
            },
        ),
        migrations.CreateModel(
            name="PersistentAgentWorkPlanStep",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("normalized_title", models.CharField(max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("todo", "To Do"),
                            ("doing", "Doing"),
                            ("done", "Done"),
                        ],
                        default="todo",
                        max_length=16,
                    ),
                ),
                ("position", models.PositiveSmallIntegerField(default=0)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("archived_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "work_plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="steps",
                        to="api.persistentagentworkplan",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "created_at"],
            },
        ),
        migrations.AddField(
            model_name="persistentagentstep",
            name="work_plan",
            field=models.ForeignKey(
                blank=True,
                help_text="Active user-visible work plan at the time this charged step was created.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="agent_steps",
                to="api.persistentagentworkplan",
            ),
        ),
        migrations.AddField(
            model_name="persistentagentstep",
            name="work_plan_step",
            field=models.ForeignKey(
                blank=True,
                help_text="Active user-visible work plan step at the time this charged step was created.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="agent_steps",
                to="api.persistentagentworkplanstep",
            ),
        ),
        migrations.AddConstraint(
            model_name="persistentagentworkplan",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="active"),
                fields=("agent",),
                name="uniq_active_work_plan_per_agent",
            ),
        ),
        migrations.AddIndex(
            model_name="persistentagentworkplan",
            index=models.Index(fields=["agent", "status", "-started_at"], name="pa_work_plan_status_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentworkplan",
            index=models.Index(fields=["agent", "-started_at"], name="pa_work_plan_recent_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentworkplanstep",
            index=models.Index(fields=["work_plan", "status", "position"], name="pa_work_step_status_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentworkplanstep",
            index=models.Index(fields=["work_plan", "normalized_title"], name="pa_work_step_title_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentstep",
            index=models.Index(fields=["work_plan", "created_at"], name="pa_step_work_plan_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentstep",
            index=models.Index(fields=["work_plan_step", "created_at"], name="pa_step_work_step_idx"),
        ),
    ]
