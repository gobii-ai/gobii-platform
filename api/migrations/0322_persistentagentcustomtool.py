from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0321_persistentagentwebsession_visibility_state"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentCustomTool",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=128)),
                ("tool_name", models.CharField(max_length=128)),
                ("description", models.TextField()),
                ("source_path", models.CharField(max_length=512)),
                ("parameters_schema", models.JSONField(blank=True, default=dict)),
                ("entrypoint", models.CharField(default="run", max_length=64)),
                ("timeout_seconds", models.PositiveIntegerField(default=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="custom_tools",
                        to="api.persistentagent",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "tool_name"],
            },
        ),
        migrations.AddIndex(
            model_name="persistentagentcustomtool",
            index=models.Index(fields=["agent", "-updated_at"], name="pa_ctool_agent_upd_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentcustomtool",
            index=models.Index(fields=["agent", "source_path"], name="pa_ctool_agent_src_idx"),
        ),
        migrations.AddConstraint(
            model_name="persistentagentcustomtool",
            constraint=models.UniqueConstraint(fields=("agent", "tool_name"), name="unique_agent_custom_tool_name"),
        ),
    ]
