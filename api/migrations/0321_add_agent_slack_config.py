from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0320_add_trial_ended_non_renewal_pause_reason"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentSlackConfig",
            fields=[
                (
                    "endpoint",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="slack_config",
                        serialize=False,
                        to="api.persistentagentcommsendpoint",
                    ),
                ),
                (
                    "workspace_id",
                    models.CharField(
                        blank=True,
                        help_text="Slack workspace (team) ID, e.g. T0123ABCDEF.",
                        max_length=64,
                    ),
                ),
                (
                    "channel_id",
                    models.CharField(
                        blank=True,
                        help_text="Default Slack channel ID for outbound messages.",
                        max_length=64,
                    ),
                ),
                (
                    "bot_token_encrypted",
                    models.BinaryField(
                        blank=True,
                        help_text="Per-agent Bot User OAuth Token (xoxb-…). Falls back to global setting.",
                        null=True,
                    ),
                ),
                (
                    "thread_policy",
                    models.CharField(
                        choices=[
                            ("auto", "Auto (thread if inbound was threaded)"),
                            ("always", "Always reply in thread"),
                            ("never", "Never use threads"),
                        ],
                        default="auto",
                        max_length=16,
                    ),
                ),
                (
                    "is_enabled",
                    models.BooleanField(db_index=True, default=False),
                ),
                (
                    "connection_last_ok_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "connection_error",
                    models.TextField(blank=True),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["is_enabled"],
                        name="agent_slack_enabled_idx",
                    ),
                ],
            },
        ),
    ]
