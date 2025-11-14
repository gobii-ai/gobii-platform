import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0186_merge_20251114_1455"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentSystemMessageBroadcast",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("body", models.TextField(help_text="Directive text sent to all persistent agents.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Admin user that initiated this broadcast.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="issued_agent_system_broadcasts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddField(
            model_name="persistentagentsystemmessage",
            name="broadcast",
            field=models.ForeignKey(
                blank=True,
                help_text="Broadcast that created this directive, if applicable.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="system_messages",
                to="api.persistentagentsystemmessagebroadcast",
            ),
        ),
    ]
