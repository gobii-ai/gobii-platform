from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0271_add_sandbox_compute_flag"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentComputeSession",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("pod_name", models.CharField(max_length=128, blank=True)),
                ("namespace", models.CharField(max_length=128)),
                ("workspace_pvc", models.CharField(max_length=128, blank=True)),
                (
                    "state",
                    models.CharField(
                        max_length=32,
                        choices=[
                            ("running", "Running"),
                            ("idle_stopping", "Idle stopping"),
                            ("stopped", "Stopped"),
                            ("error", "Error"),
                        ],
                        default="stopped",
                    ),
                ),
                ("last_activity_at", models.DateTimeField(null=True, blank=True)),
                ("lease_expires_at", models.DateTimeField(null=True, blank=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.OneToOneField(
                        related_name="compute_session",
                        on_delete=django.db.models.deletion.CASCADE,
                        to="api.persistentagent",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["state", "last_activity_at"], name="compute_state_activity_idx"),
                ],
            },
        ),
    ]
