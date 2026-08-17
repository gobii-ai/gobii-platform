import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


FLAG_NAME = "portable_agent_exports"


def add_portable_export_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    Flag.objects.update_or_create(
        name=FLAG_NAME,
        defaults={
            "everyone": False,
            "percent": 0,
            "superusers": False,
            "staff": False,
            "authenticated": False,
            "note": "Allow regular users to create portable agent migration exports.",
        },
    )


def remove_portable_export_flag(apps, schema_editor):
    apps.get_model("waffle", "Flag").objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0457_merge_20260811_1623"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.CreateModel(
            name="PortableAgentExport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("scope", models.CharField(choices=[("agent", "Agent"), ("personal", "Personal workspace"), ("organization", "Organization")], max_length=24)),
                ("scope_key", models.CharField(max_length=128)),
                ("format_version", models.CharField(default="gobii.agent-portable-export/v1", max_length=64)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("ready", "Ready"), ("ready_with_warnings", "Ready with warnings"), ("failed", "Failed"), ("expired", "Expired")], db_index=True, default="queued", max_length=24)),
                ("phase", models.CharField(default="queued", max_length=32)),
                ("total_agents", models.PositiveIntegerField(default=0)),
                ("completed_agents", models.PositiveIntegerField(default=0)),
                ("failed_agents", models.PositiveIntegerField(default=0)),
                ("warning_count", models.PositiveIntegerField(default=0)),
                ("redaction_count", models.PositiveIntegerField(default=0)),
                ("storage_key", models.CharField(blank=True, max_length=512)),
                ("archive_filename", models.CharField(blank=True, max_length=255)),
                ("archive_size_bytes", models.PositiveBigIntegerField(blank=True, null=True)),
                ("archive_sha256", models.CharField(blank=True, max_length=64)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_message", models.CharField(blank=True, max_length=512)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("email_sent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="portable_exports", to="api.persistentagent")),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="portable_agent_exports", to="api.organization")),
                ("requester", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="portable_agent_exports", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="PortableAgentExportItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_agent_id", models.UUIDField()),
                ("source_agent_name", models.CharField(max_length=255)),
                ("folder_name", models.CharField(max_length=320)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("ready", "Ready"), ("failed", "Failed")], default="queued", max_length=16)),
                ("snapshot_at", models.DateTimeField(blank=True, null=True)),
                ("message_count", models.PositiveIntegerField(default=0)),
                ("step_count", models.PositiveIntegerField(default=0)),
                ("file_count", models.PositiveIntegerField(default=0)),
                ("warning_count", models.PositiveIntegerField(default=0)),
                ("redaction_count", models.PositiveIntegerField(default=0)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_message", models.CharField(blank=True, max_length=512)),
                ("agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="portable_export_items", to="api.persistentagent")),
                ("export", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="api.portableagentexport")),
            ],
            options={"ordering": ["source_agent_name", "source_agent_id"]},
        ),
        migrations.AddConstraint(
            model_name="portableagentexport",
            constraint=models.UniqueConstraint(condition=models.Q(("status__in", ["queued", "running"])), fields=("requester", "scope_key"), name="uniq_active_portable_export_scope"),
        ),
        migrations.AddIndex(
            model_name="portableagentexport",
            index=models.Index(fields=["requester", "scope_key", "-created_at"], name="pa_export_req_scope_idx"),
        ),
        migrations.AddIndex(
            model_name="portableagentexport",
            index=models.Index(fields=["status", "expires_at"], name="pa_export_status_exp_idx"),
        ),
        migrations.AddConstraint(
            model_name="portableagentexportitem",
            constraint=models.UniqueConstraint(fields=("export", "source_agent_id"), name="uniq_export_source_agent"),
        ),
        migrations.AddConstraint(
            model_name="portableagentexportitem",
            constraint=models.UniqueConstraint(fields=("export", "folder_name"), name="uniq_export_agent_folder"),
        ),
        migrations.AddIndex(
            model_name="portableagentexportitem",
            index=models.Index(fields=["export", "status"], name="pa_export_item_status_idx"),
        ),
        migrations.RunPython(add_portable_export_flag, remove_portable_export_flag),
    ]
