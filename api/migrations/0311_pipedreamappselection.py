from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0310_imagegenerationllmtier_use_case"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PipedreamAppSelection",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("selected_app_slugs", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pipedream_app_selections",
                        to="api.organization",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pipedream_app_selections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={},
        ),
        migrations.AddIndex(
            model_name="pipedreamappselection",
            index=models.Index(fields=["organization"], name="pd_app_selection_org_idx"),
        ),
        migrations.AddIndex(
            model_name="pipedreamappselection",
            index=models.Index(fields=["user"], name="pd_app_selection_user_idx"),
        ),
        migrations.AddConstraint(
            model_name="pipedreamappselection",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(organization__isnull=False, user__isnull=True)
                    | models.Q(organization__isnull=True, user__isnull=False)
                ),
                name="pd_app_selection_exactly_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="pipedreamappselection",
            constraint=models.UniqueConstraint(
                condition=models.Q(organization__isnull=False),
                fields=("organization",),
                name="unique_pipedream_app_selection_org",
            ),
        ),
        migrations.AddConstraint(
            model_name="pipedreamappselection",
            constraint=models.UniqueConstraint(
                condition=models.Q(user__isnull=False),
                fields=("user",),
                name="unique_pipedream_app_selection_user",
            ),
        ),
    ]
