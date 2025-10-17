from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0163_browsermodelendpoint_max_output_tokens"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentWebhook",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=128)),
                ("url", models.URLField(max_length=1024)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("last_triggered_at", models.DateTimeField(blank=True, null=True)),
                ("last_response_status", models.IntegerField(blank=True, null=True)),
                ("last_error_message", models.TextField(blank=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="webhooks",
                        to="api.persistentagent",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddConstraint(
            model_name="persistentagentwebhook",
            constraint=models.UniqueConstraint(
                fields=("agent", "name"),
                name="uniq_agent_webhook_name",
            ),
        ),
        migrations.AddIndex(
            model_name="persistentagentwebhook",
            index=models.Index(
                fields=("agent", "created_at"),
                name="pa_webhook_agent_created_idx",
            ),
        ),
    ]
