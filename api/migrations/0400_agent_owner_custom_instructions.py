import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0399_persistentagenttemplate_organization"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentOwnerCustomInstructions",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("instructions", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_owner_custom_instructions",
                        to="api.organization",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_agent_owner_custom_instructions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_owner_custom_instructions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "agent owner custom instructions",
                "verbose_name_plural": "agent owner custom instructions",
            },
        ),
        migrations.AddConstraint(
            model_name="agentownercustominstructions",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("organization__isnull", True), ("user__isnull", False))
                    | models.Q(("organization__isnull", False), ("user__isnull", True))
                ),
                name="agent_owner_ci_exactly_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentownercustominstructions",
            constraint=models.UniqueConstraint(
                condition=models.Q(("user__isnull", False)),
                fields=("user",),
                name="unique_agent_owner_ci_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentownercustominstructions",
            constraint=models.UniqueConstraint(
                condition=models.Q(("organization__isnull", False)),
                fields=("organization",),
                name="unique_agent_owner_ci_org",
            ),
        ),
    ]
