import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0417_persistentagentuseractionevent"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentOwnerCategoryProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("categories", models.JSONField(blank=True, default=list)),
                ("source_fingerprint", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_category_profiles",
                        to="api.organization",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_category_profiles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "agent owner category profile",
                "verbose_name_plural": "agent owner category profiles",
            },
        ),
        migrations.AddConstraint(
            model_name="agentownercategoryprofile",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("organization__isnull", True), ("user__isnull", False))
                    | models.Q(("organization__isnull", False), ("user__isnull", True))
                ),
                name="agent_owner_category_profile_exactly_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentownercategoryprofile",
            constraint=models.UniqueConstraint(
                condition=models.Q(("user__isnull", False)),
                fields=("user",),
                name="unique_agent_owner_category_profile_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentownercategoryprofile",
            constraint=models.UniqueConstraint(
                condition=models.Q(("organization__isnull", False)),
                fields=("organization",),
                name="unique_agent_owner_category_profile_org",
            ),
        ),
    ]
