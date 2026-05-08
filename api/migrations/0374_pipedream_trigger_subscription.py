import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0373_alter_organizationbilling_execution_pause_reason_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentPipedreamTriggerSubscription",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("app_slug", models.CharField(max_length=64)),
                ("event_type", models.CharField(max_length=64)),
                ("platform_channel", models.CharField(max_length=255)),
                ("platform_channel_name", models.CharField(blank=True, max_length=255)),
                ("trigger_key", models.CharField(max_length=128)),
                ("trigger_version", models.CharField(blank=True, max_length=32)),
                ("external_user_id", models.CharField(max_length=64)),
                ("deployed_trigger_id", models.CharField(blank=True, max_length=128)),
                ("configured_props", models.JSONField(blank=True, default=dict)),
                ("webhook_secret_encrypted", models.BinaryField(blank=True, null=True)),
                ("signing_key_encrypted", models.BinaryField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("disabled", "Disabled"), ("error", "Error")],
                        db_index=True,
                        default="active",
                        max_length=16,
                    ),
                ),
                ("last_error", models.TextField(blank=True)),
                ("last_event_at", models.DateTimeField(blank=True, null=True)),
                ("last_deployed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pipedream_trigger_subscriptions",
                        to="api.persistentagent",
                    ),
                ),
            ],
            options={
                "ordering": ["app_slug", "event_type", "platform_channel"],
            },
        ),
        migrations.AddIndex(
            model_name="persistentagentpipedreamtriggersubscription",
            index=models.Index(fields=["agent", "status", "app_slug"], name="pa_pd_trig_agent_status_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentpipedreamtriggersubscription",
            index=models.Index(fields=["deployed_trigger_id"], name="pa_pd_trig_deployed_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentpipedreamtriggersubscription",
            index=models.Index(fields=["app_slug", "event_type"], name="pa_pd_trig_app_event_idx"),
        ),
        migrations.AddConstraint(
            model_name="persistentagentpipedreamtriggersubscription",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "active")),
                fields=("agent", "app_slug", "event_type", "platform_channel"),
                name="uniq_active_agent_pd_trigger_channel",
            ),
        ),
    ]
