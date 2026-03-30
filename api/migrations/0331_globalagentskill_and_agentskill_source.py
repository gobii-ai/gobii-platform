from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0330_toolconfig_tool_search_auto_enable_apps"),
    ]

    operations = [
        migrations.CreateModel(
            name="GlobalAgentSkill",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=128, unique=True)),
                ("description", models.TextField(blank=True)),
                ("tools", models.JSONField(blank=True, default=list)),
                ("instructions", models.TextField()),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddIndex(
            model_name="globalagentskill",
            index=models.Index(fields=["is_active", "name"], name="ga_skill_active_name_idx"),
        ),
        migrations.AddIndex(
            model_name="globalagentskill",
            index=models.Index(fields=["-updated_at"], name="ga_skill_updated_idx"),
        ),
        migrations.AddField(
            model_name="persistentagentskill",
            name="global_skill",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="imported_agent_skills",
                to="api.globalagentskill",
            ),
        ),
    ]
