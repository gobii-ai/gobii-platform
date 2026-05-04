import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0365_persistent_agent_completion_helper_types"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentPlanDeliverable",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("file", "File"), ("message", "Message")], max_length=16)),
                ("label", models.CharField(blank=True, max_length=255)),
                ("path", models.CharField(blank=True, max_length=1024)),
                ("position", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plan_deliverables",
                        to="api.persistentagent",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plan_deliverables",
                        to="api.persistentagentmessage",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="persistentagentplandeliverable",
            index=models.Index(fields=["agent", "kind", "position"], name="plan_deliv_agent_kind_idx"),
        ),
    ]
