import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0426_persistentagenttoolcall_display_metadata"),
        ("api", "0427_add_contact_auto_approve_email_flag"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentLinkReference",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("url", models.TextField()),
                ("url_hash", models.CharField(editable=False, max_length=64)),
                (
                    "source_kind",
                    models.CharField(
                        choices=[
                            ("inbound_message", "Inbound message"),
                            ("tool_result", "Tool result"),
                        ],
                        max_length=32,
                    ),
                ),
                ("source_object_id", models.CharField(blank=True, max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="link_references",
                        to="api.persistentagent",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("agent", "url_hash"),
                        name="unique_agent_link_url_hash",
                    ),
                ],
            },
        ),
    ]
