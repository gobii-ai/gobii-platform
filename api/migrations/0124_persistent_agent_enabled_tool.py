from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0123_persistent_endpoint_parallel_tool_calls"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentEnabledTool",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("tool_full_name", models.CharField(max_length=256)),
                ("tool_server", models.CharField(max_length=64, blank=True)),
                ("tool_name", models.CharField(max_length=128, blank=True)),
                ("enabled_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(null=True, blank=True, db_index=True)),
                ("usage_count", models.PositiveIntegerField(default=0)),
                (
                    "agent",
                    models.ForeignKey(
                        to="api.persistentagent",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="enabled_tools",
                    ),
                ),
            ],
            options={
                "ordering": ["-last_used_at", "-enabled_at"],
            },
        ),
        migrations.AddIndex(
            model_name="persistentagentenabledtool",
            index=models.Index(fields=["agent", "last_used_at"], name="pa_en_tool_agent_lu_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentenabledtool",
            index=models.Index(fields=["tool_full_name"], name="pa_en_tool_name_idx"),
        ),
        migrations.AddConstraint(
            model_name="persistentagentenabledtool",
            constraint=models.UniqueConstraint(
                fields=["agent", "tool_full_name"], name="unique_agent_tool_full_name"
            ),
        ),
    ]
