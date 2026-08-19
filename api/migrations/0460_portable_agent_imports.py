import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


FLAG_NAME = "portable_agent_imports"


def add_portable_import_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    Flag.objects.update_or_create(
        name=FLAG_NAME,
        defaults={
            "everyone": False,
            "percent": 0,
            "superusers": False,
            "staff": False,
            "authenticated": False,
            "note": "Allow imports of portable Gobii agent migration archives in proprietary deployments.",
        },
    )


def remove_portable_import_flag(apps, schema_editor):
    apps.get_model("waffle", "Flag").objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0459_portable_export_artifact_cleanup"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.AlterField(
            model_name="portableagentexport",
            name="format_version",
            field=models.CharField(default="gobii.agent-portable-export/v2", max_length=64),
        ),
        migrations.CreateModel(
            name="PortableAgentImport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("target_type", models.CharField(choices=[("personal", "Personal workspace"), ("organization", "Organization")], max_length=24)),
                ("target_key", models.CharField(max_length=128)),
                ("format_version", models.CharField(blank=True, max_length=64)),
                ("status", models.CharField(choices=[("validating", "Validating"), ("awaiting_selection", "Awaiting selection"), ("queued", "Queued"), ("running", "Running"), ("completed", "Completed"), ("completed_with_warnings", "Completed with warnings"), ("failed", "Failed"), ("expired", "Expired")], db_index=True, default="validating", max_length=32)),
                ("phase", models.CharField(default="validating", max_length=32)),
                ("storage_key", models.CharField(blank=True, max_length=512)),
                ("archive_filename", models.CharField(blank=True, max_length=255)),
                ("archive_size_bytes", models.PositiveBigIntegerField(blank=True, null=True)),
                ("archive_sha256", models.CharField(blank=True, max_length=64)),
                ("total_agents", models.PositiveIntegerField(default=0)),
                ("selected_agents", models.PositiveIntegerField(default=0)),
                ("completed_agents", models.PositiveIntegerField(default=0)),
                ("failed_agents", models.PositiveIntegerField(default=0)),
                ("warning_count", models.PositiveIntegerField(default=0)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_message", models.CharField(blank=True, max_length=512)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="portable_agent_imports", to="api.organization")),
                ("requester", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="portable_agent_imports", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="PortableAgentImportItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_agent_id", models.UUIDField()),
                ("source_agent_name", models.CharField(max_length=255)),
                ("folder_name", models.CharField(max_length=320)),
                ("snapshot_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(choices=[("available", "Available"), ("unavailable", "Unavailable"), ("selected", "Selected"), ("provisioning", "Provisioning"), ("ready", "Ready"), ("failed", "Failed"), ("skipped", "Skipped")], default="available", max_length=24)),
                ("requested_name", models.CharField(blank=True, max_length=255)),
                ("message_count", models.PositiveIntegerField(default=0)),
                ("step_count", models.PositiveIntegerField(default=0)),
                ("file_count", models.PositiveIntegerField(default=0)),
                ("warning_count", models.PositiveIntegerField(default=0)),
                ("warnings", models.JSONField(blank=True, default=list)),
                ("compatibility", models.JSONField(blank=True, default=dict)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_message", models.CharField(blank=True, max_length=512)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("import_job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="api.portableagentimport")),
                ("imported_agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="portable_import_items", to="api.persistentagent")),
            ],
            options={"ordering": ["source_agent_name", "source_agent_id"]},
        ),
        migrations.CreateModel(
            name="PortableAgentImportArtifactCleanup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("storage_key", models.CharField(max_length=512, unique=True)),
                ("source_import_id", models.UUIDField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.CreateModel(
            name="PortableAgentMigrationReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_format_version", models.CharField(max_length=64)),
                ("source_agent_id", models.UUIDField()),
                ("source_snapshot_at", models.DateTimeField(blank=True, null=True)),
                ("source_was_active", models.BooleanField(default=False)),
                ("report", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("agent", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="portable_migration_report", to="api.persistentagent")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="portableagentimport",
            index=models.Index(fields=["requester", "target_key", "-created_at"], name="pa_import_req_target_idx"),
        ),
        migrations.AddIndex(
            model_name="portableagentimport",
            index=models.Index(fields=["status", "expires_at"], name="pa_import_status_exp_idx"),
        ),
        migrations.AddConstraint(
            model_name="portableagentimport",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(target_type="personal", organization__isnull=True)
                    | models.Q(target_type="organization", organization__isnull=False)
                ),
                name="pa_import_target_matches_org",
            ),
        ),
        migrations.AddConstraint(
            model_name="portableagentimportitem",
            constraint=models.UniqueConstraint(fields=("import_job", "source_agent_id"), name="uniq_import_source_agent"),
        ),
        migrations.AddConstraint(
            model_name="portableagentimportitem",
            constraint=models.UniqueConstraint(fields=("import_job", "folder_name"), name="uniq_import_agent_folder"),
        ),
        migrations.AddIndex(
            model_name="portableagentimportitem",
            index=models.Index(fields=["import_job", "status"], name="pa_import_item_status_idx"),
        ),
        migrations.RunPython(add_portable_import_flag, remove_portable_import_flag),
    ]
