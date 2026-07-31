import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


FLAG_NAME = "computer_cpp_integration"


def initialize_computer_integration(apps, schema_editor):
    MCPServerConfig = apps.get_model("api", "MCPServerConfig")
    MCPServerConfig.objects.filter(command__gt="").update(transport="stdio")
    MCPServerConfig.objects.filter(command="", url__gt="").update(transport="streamable_http")

    Flag = apps.get_model("waffle", "Flag")
    Flag.objects.update_or_create(
        name=FLAG_NAME,
        defaults={"everyone": False, "superusers": False, "staff": False, "authenticated": False},
    )


def remove_computer_flag(apps, schema_editor):
    apps.get_model("waffle", "Flag").objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0445_judge_lifecycle_and_email_bcc"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpserverconfig",
            name="transport",
            field=models.CharField(
                choices=[
                    ("stdio", "STDIO"),
                    ("streamable_http", "Streamable HTTP"),
                    ("computer_relay", "Computer relay"),
                ],
                default="streamable_http",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="ComputerDevice",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("machine_identifier_digest", models.CharField(max_length=64, unique=True)),
                ("display_name", models.CharField(max_length=128)),
                (
                    "platform",
                    models.CharField(
                        choices=[("macos", "macOS"), ("windows", "Windows")],
                        max_length=32,
                    ),
                ),
                ("architecture", models.CharField(max_length=32)),
                ("client_version", models.CharField(max_length=32)),
                ("protocol_version", models.PositiveIntegerField()),
                ("credential_generation", models.PositiveIntegerField(default=1)),
                ("is_paused", models.BooleanField(default=False)),
                ("revoked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="computer_devices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["owner", "revoked_at"], name="computer_owner_revoked_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ComputerPairingSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("device_code_digest", models.CharField(max_length=64, unique=True)),
                ("user_code_digest", models.CharField(max_length=64)),
                ("machine_identifier_digest", models.CharField(max_length=64)),
                ("display_name", models.CharField(max_length=128)),
                (
                    "platform",
                    models.CharField(
                        choices=[("macos", "macOS"), ("windows", "Windows")],
                        max_length=32,
                    ),
                ),
                ("architecture", models.CharField(max_length=32)),
                ("client_version", models.CharField(max_length=32)),
                ("protocol_version", models.PositiveIntegerField()),
                ("app_manifest", models.JSONField(default=list)),
                ("selected_app_keys", models.JSONField(default=list)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("denied", "Denied"),
                            ("redeemed", "Redeemed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("last_polled_at", models.DateTimeField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("denied_at", models.DateTimeField(blank=True, null=True)),
                ("redeemed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_computer_pairings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "selected_agent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="computer_pairing_sessions",
                        to="api.persistentagent",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ComputerDeviceCredential",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("family_id", models.UUIDField(db_index=True, default=uuid.uuid4)),
                ("generation", models.PositiveIntegerField()),
                ("token_digest", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credentials",
                        to="api.computerdevice",
                    ),
                ),
                (
                    "replaced_by",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="replaces",
                        to="api.computerdevicecredential",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ComputerDeviceAssignment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("granted_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="computer_assignments",
                        to="api.persistentagent",
                    ),
                ),
                (
                    "device",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignment",
                        to="api.computerdevice",
                    ),
                ),
                (
                    "granted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="granted_computer_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="computer_device_assignments",
                        to="api.organization",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["agent", "revoked_at"], name="computer_agent_revoked_idx"),
                    models.Index(fields=["organization", "revoked_at"], name="computer_org_revoked_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ComputerDeviceApp",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("app_key", models.SlugField(max_length=80)),
                ("display_name", models.CharField(max_length=128)),
                (
                    "app_type",
                    models.CharField(
                        choices=[("bundled", "Bundled"), ("custom", "Custom")],
                        max_length=16,
                    ),
                ),
                ("reported_schema_hash", models.CharField(max_length=64)),
                ("approved_schema_hash", models.CharField(blank=True, max_length=64)),
                (
                    "approval_state",
                    models.CharField(
                        choices=[
                            ("approved", "Approved"),
                            ("pending_approval", "Pending approval"),
                            ("disabled", "Disabled"),
                        ],
                        default="pending_approval",
                        max_length=24,
                    ),
                ),
                ("is_available", models.BooleanField(default=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="apps",
                        to="api.computerdevice",
                    ),
                ),
                (
                    "mcp_server_config",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="computer_device_app",
                        to="api.mcpserverconfig",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("device", "app_key"),
                        name="unique_computer_device_app",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ComputerRelayArtifact",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("storage_key", models.CharField(max_length=512)),
                ("mime_type", models.CharField(max_length=64)),
                ("byte_count", models.PositiveIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="relay_artifacts",
                        to="api.computerdevice",
                    ),
                ),
            ],
        ),
        migrations.RunPython(initialize_computer_integration, remove_computer_flag),
    ]
