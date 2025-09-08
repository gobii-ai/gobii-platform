from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0112_browseruseagenttask_cached_tokens_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentEmailAccount",
            fields=[
                ("endpoint", models.OneToOneField(primary_key=True, serialize=False, on_delete=django.db.models.deletion.CASCADE, related_name="agentemailaccount", to="api.persistentagentcommsendpoint")),
                ("smtp_host", models.CharField(blank=True, max_length=255)),
                ("smtp_port", models.PositiveIntegerField(blank=True, null=True)),
                ("smtp_security", models.CharField(choices=[("ssl", "SSL"), ("starttls", "STARTTLS"), ("none", "None")], default="starttls", max_length=16)),
                ("smtp_auth", models.CharField(choices=[("none", "None"), ("plain", "PLAIN"), ("login", "LOGIN")], default="login", max_length=16)),
                ("smtp_username", models.CharField(blank=True, max_length=255)),
                ("smtp_password_encrypted", models.BinaryField(blank=True, null=True)),
                ("is_outbound_enabled", models.BooleanField(db_index=True, default=False)),
                ("imap_host", models.CharField(blank=True, max_length=255)),
                ("imap_port", models.PositiveIntegerField(blank=True, null=True)),
                ("imap_security", models.CharField(choices=[("ssl", "SSL"), ("starttls", "STARTTLS"), ("none", "None")], default="ssl", max_length=16)),
                ("imap_username", models.CharField(blank=True, max_length=255)),
                ("imap_password_encrypted", models.BinaryField(blank=True, null=True)),
                ("imap_folder", models.CharField(default="INBOX", max_length=128)),
                ("is_inbound_enabled", models.BooleanField(default=False)),
                ("poll_interval_sec", models.PositiveIntegerField(default=120)),
                ("last_polled_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_uid", models.CharField(blank=True, max_length=64)),
                ("backoff_until", models.DateTimeField(blank=True, null=True)),
                ("connection_last_ok_at", models.DateTimeField(blank=True, null=True)),
                ("connection_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
        migrations.AddIndex(
            model_name="agentemailaccount",
            index=models.Index(fields=["is_outbound_enabled"], name="agent_email_outbound_idx"),
        ),
        migrations.AddIndex(
            model_name="agentemailaccount",
            index=models.Index(fields=["endpoint"], name="agent_email_endpoint_idx"),
        ),
    ]

