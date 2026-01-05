from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


def backfill_agent_email_connection_mode(apps, schema_editor):
    agent_email_account = apps.get_model('api', 'AgentEmailAccount')
    using = schema_editor.connection.alias
    oauth_filter = models.Q(smtp_auth='oauth2') | models.Q(imap_auth='oauth2')
    agent_email_account.objects.using(using).exclude(oauth_filter).update(connection_mode='custom')


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0246_add_google_docs_to_pipedream_prefetch'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentemailaccount',
            name='smtp_auth',
            field=models.CharField(choices=[('none', 'None'), ('plain', 'PLAIN'), ('login', 'LOGIN'), ('oauth2', 'OAuth 2.0')], default='login', max_length=16),
        ),
        migrations.AddField(
            model_name='agentemailaccount',
            name='imap_auth',
            field=models.CharField(choices=[('none', 'None'), ('login', 'LOGIN'), ('oauth2', 'OAuth 2.0')], default='login', max_length=16),
        ),
        migrations.AddField(
            model_name='agentemailaccount',
            name='connection_mode',
            field=models.CharField(choices=[('custom', 'Custom SMTP/IMAP'), ('oauth2', 'OAuth 2.0')], default='oauth2', max_length=16),
        ),
        migrations.RunPython(backfill_agent_email_connection_mode, migrations.RunPython.noop),
        migrations.CreateModel(
            name='AgentEmailOAuthCredential',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('provider', models.CharField(blank=True, max_length=64)),
                ('client_id', models.CharField(blank=True, max_length=256)),
                ('client_secret_encrypted', models.BinaryField(blank=True, null=True)),
                ('access_token_encrypted', models.BinaryField(blank=True, null=True)),
                ('refresh_token_encrypted', models.BinaryField(blank=True, null=True)),
                ('id_token_encrypted', models.BinaryField(blank=True, null=True)),
                ('token_type', models.CharField(blank=True, max_length=32)),
                ('scope', models.CharField(blank=True, max_length=512)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('account', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='oauth_credential', to='api.agentemailaccount')),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='agent_email_oauth_credentials', to='api.organization')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_email_oauth_credentials', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='AgentEmailOAuthSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('state', models.CharField(max_length=256, unique=True)),
                ('redirect_uri', models.CharField(blank=True, max_length=512)),
                ('scope', models.CharField(blank=True, max_length=512)),
                ('code_challenge', models.CharField(blank=True, max_length=256)),
                ('code_challenge_method', models.CharField(blank=True, max_length=16)),
                ('token_endpoint', models.CharField(blank=True, max_length=512)),
                ('client_id', models.CharField(blank=True, max_length=256)),
                ('client_secret_encrypted', models.BinaryField(blank=True, null=True)),
                ('code_verifier_encrypted', models.BinaryField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='oauth_sessions', to='api.agentemailaccount')),
                ('initiated_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_email_oauth_sessions', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='agent_email_oauth_sessions', to='api.organization')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_email_oauth_user_sessions', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='agentemailoauthcredential',
            index=models.Index(fields=['organization'], name='email_oauth_cred_org_idx'),
        ),
        migrations.AddIndex(
            model_name='agentemailoauthcredential',
            index=models.Index(fields=['user'], name='email_oauth_cred_user_idx'),
        ),
        migrations.AddIndex(
            model_name='agentemailoauthsession',
            index=models.Index(fields=['expires_at'], name='email_oauth_session_exp_idx'),
        ),
    ]
