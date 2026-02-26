from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0249_email_oauth_models'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='GoogleWorkspaceCredential',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('google_account_email', models.EmailField(blank=True, max_length=255)),
                ('scope_tier', models.CharField(blank=True, max_length=32)),
                ('scopes', models.TextField(blank=True, help_text='Space-separated scopes granted by the user.')),
                ('access_token_encrypted', models.BinaryField(blank=True, null=True)),
                ('refresh_token_encrypted', models.BinaryField(blank=True, null=True)),
                ('id_token_encrypted', models.BinaryField(blank=True, null=True)),
                ('token_type', models.CharField(blank=True, max_length=32)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='google_workspace_credentials', to='api.organization')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='google_workspace_credentials', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='AgentGoogleWorkspaceBinding',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('scope_tier', models.CharField(blank=True, max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('agent', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='google_workspace_binding', to='api.persistentagent')),
                ('credential', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bindings', to='api.googleworkspacecredential')),
            ],
        ),
        migrations.AddIndex(
            model_name='googleworkspacecredential',
            index=models.Index(fields=['organization'], name='gws_cred_org_idx'),
        ),
        migrations.AddIndex(
            model_name='googleworkspacecredential',
            index=models.Index(fields=['user'], name='gws_cred_user_idx'),
        ),
        migrations.AddIndex(
            model_name='googleworkspacecredential',
            index=models.Index(fields=['google_account_email'], name='gws_cred_email_idx'),
        ),
        migrations.AddIndex(
            model_name='agentgoogleworkspacebinding',
            index=models.Index(fields=['agent'], name='gws_binding_agent_idx'),
        ),
        migrations.AddIndex(
            model_name='agentgoogleworkspacebinding',
            index=models.Index(fields=['credential'], name='gws_binding_credential_idx'),
        ),
    ]
