from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0126_remove_json_tool_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="PipedreamConnectSession",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)),
                ("external_user_id", models.CharField(max_length=64)),
                ("conversation_id", models.CharField(max_length=64)),
                ("app_slug", models.CharField(max_length=64)),
                ("connect_token", models.CharField(max_length=128, unique=True, blank=True)),
                ("connect_link_url", models.TextField(blank=True)),
                ("expires_at", models.DateTimeField(null=True, blank=True)),
                ("webhook_secret", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        max_length=16,
                        choices=[("pending", "Pending"), ("success", "Success"), ("error", "Error")],
                        default="pending",
                        db_index=True,
                    ),
                ),
                ("account_id", models.CharField(max_length=64, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        to="api.persistentagent",
                        on_delete=models.deletion.CASCADE,
                        related_name="pipedream_connect_sessions",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["agent", "status", "-created_at"], name="pd_connect_agent_idx"),
                ],
            },
        ),
    ]

