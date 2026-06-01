import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0388_native_integration_secret_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="NativeIntegrationGrantedFile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("provider_key", models.CharField(max_length=128)),
                ("external_file_id", models.CharField(max_length=255)),
                ("name", models.CharField(max_length=512)),
                ("mime_type", models.CharField(max_length=255)),
                ("url", models.TextField(blank=True)),
                ("last_selected_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="native_integration_granted_files",
                        to="api.organization",
                    ),
                ),
                (
                    "selected_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="native_integration_file_selections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="native_integration_granted_files",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["name", "external_file_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="nativeintegrationgrantedfile",
            constraint=models.CheckConstraint(
                condition=(
                    (models.Q(("user__isnull", False), ("organization__isnull", True)))
                    | (models.Q(("user__isnull", True), ("organization__isnull", False)))
                ),
                name="native_file_exactly_one_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="nativeintegrationgrantedfile",
            constraint=models.UniqueConstraint(
                condition=models.Q(("user__isnull", False)),
                fields=("user", "provider_key", "external_file_id"),
                name="unique_native_file_user_provider_ext",
            ),
        ),
        migrations.AddConstraint(
            model_name="nativeintegrationgrantedfile",
            constraint=models.UniqueConstraint(
                condition=models.Q(("organization__isnull", False)),
                fields=("organization", "provider_key", "external_file_id"),
                name="unique_native_file_org_provider_ext",
            ),
        ),
        migrations.AddIndex(
            model_name="nativeintegrationgrantedfile",
            index=models.Index(fields=["user", "provider_key", "mime_type"], name="nif_user_provider_mime_idx"),
        ),
        migrations.AddIndex(
            model_name="nativeintegrationgrantedfile",
            index=models.Index(fields=["organization", "provider_key", "mime_type"], name="nif_org_provider_mime_idx"),
        ),
        migrations.AddIndex(
            model_name="nativeintegrationgrantedfile",
            index=models.Index(fields=["provider_key", "-last_selected_at"], name="nif_provider_selected_idx"),
        ),
    ]
