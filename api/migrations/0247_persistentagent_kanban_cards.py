from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0246_add_google_docs_to_pipedream_prefetch"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentKanbanCard",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                (
                    "status",
                    models.CharField(
                        max_length=16,
                        choices=[("todo", "To Do"), ("doing", "Doing"), ("done", "Done")],
                        default="todo",
                    ),
                ),
                ("priority", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("completed_at", models.DateTimeField(null=True, blank=True)),
                (
                    "assigned_agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="kanban_cards",
                        to="api.persistentagent",
                    ),
                ),
            ],
            options={
                "ordering": ["-priority", "created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="persistentagentkanbancard",
            index=models.Index(
                fields=["assigned_agent", "status", "-priority"],
                name="kanban_agent_status_pri_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="persistentagentkanbancard",
            index=models.Index(
                fields=["assigned_agent", "status", "-completed_at"],
                name="kanban_agent_status_done_idx",
            ),
        ),
    ]
