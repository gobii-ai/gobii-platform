import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0416_merge_20260708_1356"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentOwnerTemplateRecommendationState",
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
                        related_name="template_recommendation_states",
                        to="api.organization",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="template_recommendation_states",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "agent owner template recommendation state",
                "verbose_name_plural": "agent owner template recommendation states",
            },
        ),
        migrations.AddConstraint(
            model_name="agentownertemplaterecommendationstate",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("organization__isnull", True), ("user__isnull", False))
                    | models.Q(("organization__isnull", False), ("user__isnull", True))
                ),
                name="agent_owner_template_rec_exactly_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentownertemplaterecommendationstate",
            constraint=models.UniqueConstraint(
                condition=models.Q(("user__isnull", False)),
                fields=("user",),
                name="unique_template_rec_state_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentownertemplaterecommendationstate",
            constraint=models.UniqueConstraint(
                condition=models.Q(("organization__isnull", False)),
                fields=("organization",),
                name="unique_template_rec_state_org",
            ),
        ),
    ]
