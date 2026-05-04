from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0366_persistent_agent_plan_deliverables"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentskill",
            name="last_used_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="persistentagentskill",
            name="usage_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name="persistentagentskill",
            index=models.Index(fields=["agent", "last_used_at"], name="pa_skill_agent_lu_idx"),
        ),
        migrations.CreateModel(
            name="PersistentAgentSystemSkillState",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("skill_key", models.CharField(max_length=128)),
                ("is_enabled", models.BooleanField(default=True)),
                ("enabled_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("usage_count", models.PositiveIntegerField(default=0)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="system_skill_states",
                        to="api.persistentagent",
                    ),
                ),
            ],
            options={
                "ordering": ["-last_used_at", "-enabled_at"],
                "indexes": [
                    models.Index(fields=["agent", "is_enabled", "last_used_at"], name="pa_sys_skill_agent_lu_idx"),
                    models.Index(fields=["skill_key"], name="pa_sys_skill_key_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("agent", "skill_key"),
                        name="unique_agent_system_skill_state",
                    )
                ],
            },
        ),
    ]
