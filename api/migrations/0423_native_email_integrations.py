from django.db import migrations, models
import django.db.models.deletion


def backfill_email_integrations(apps, schema_editor):
    AgentEmailAccount = apps.get_model("api", "AgentEmailAccount")
    AgentEmailIntegration = apps.get_model("api", "AgentEmailIntegration")

    accounts = AgentEmailAccount.objects.select_related("endpoint").all()
    for account in accounts.iterator():
        agent_id = account.endpoint.owner_agent_id
        if not agent_id:
            continue

        has_oauth = apps.get_model("api", "AgentEmailOAuthCredential").objects.filter(
            account_id=account.pk
        ).exists()
        has_custom_secrets = bool(
            account.smtp_password_encrypted or account.imap_password_encrypted
        )
        custom_account_id = account.pk if not has_oauth or has_custom_secrets else None
        oauth_account_id = account.pk if has_oauth else None

        if has_oauth and (
            account.connection_mode == "oauth2"
            or account.is_outbound_enabled
            or account.is_inbound_enabled
        ):
            active_mode = "oauth"
        elif not has_oauth and (
            account.is_outbound_enabled
            or account.is_inbound_enabled
            or account.smtp_host
            or account.imap_host
        ):
            active_mode = "custom"
        else:
            active_mode = "none"

        integration, created = AgentEmailIntegration.objects.get_or_create(
            agent_id=agent_id,
            defaults={
                "active_mode": active_mode,
                "custom_account_id": custom_account_id,
                "oauth_account_id": oauth_account_id,
            },
        )
        if created:
            continue
        updates = []
        if custom_account_id and not integration.custom_account_id:
            integration.custom_account_id = custom_account_id
            updates.append("custom_account")
        if oauth_account_id and not integration.oauth_account_id:
            integration.oauth_account_id = oauth_account_id
            updates.append("oauth_account")
        if integration.active_mode == "none" and active_mode != "none":
            integration.active_mode = active_mode
            updates.append("active_mode")
        if updates:
            integration.save(update_fields=updates)


def remove_unrepresentable_oauth_sessions(apps, schema_editor):
    """Remove sessions that cannot be represented by the pre-0422 schema.

    Native email OAuth starts before an AgentEmailAccount exists, so these
    short-lived authorization sessions intentionally have no account.  The old
    AgentEmailOAuthSession model required one; delete only those incomplete
    sessions before restoring its NOT NULL constraint during a rollback.
    """
    NativeIntegrationOAuthSession = apps.get_model(
        "api", "NativeIntegrationOAuthSession"
    )
    NativeIntegrationOAuthSession.objects.using(schema_editor.connection.alias).filter(
        account_id__isnull=True
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0422_update_pretrained_employee_descriptions"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="AgentEmailOAuthSession",
            new_name="NativeIntegrationOAuthSession",
        ),
        migrations.AddField(
            model_name="agentemailaccount",
            name="imap_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="agentemailaccount",
            name="imap_last_ok_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentemailaccount",
            name="smtp_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="agentemailaccount",
            name="smtp_last_ok_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="nativeintegrationoauthsession",
            name="account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="oauth_sessions",
                to="api.agentemailaccount",
            ),
        ),
        migrations.AddField(
            model_name="nativeintegrationoauthsession",
            name="agent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="native_integration_oauth_sessions",
                to="api.persistentagent",
            ),
        ),
        migrations.AddField(
            model_name="nativeintegrationoauthsession",
            name="provider_key",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.CreateModel(
            name="AgentEmailIntegration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("active_mode", models.CharField(choices=[("none", "None"), ("custom", "Custom SMTP/IMAP"), ("oauth", "OAuth")], default="none", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("agent", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="email_integration", to="api.persistentagent")),
                ("custom_account", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="custom_email_integrations", to="api.agentemailaccount")),
                ("oauth_account", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="oauth_email_integrations", to="api.agentemailaccount")),
            ],
        ),
        migrations.RunPython(
            backfill_email_integrations,
            remove_unrepresentable_oauth_sessions,
        ),
    ]
