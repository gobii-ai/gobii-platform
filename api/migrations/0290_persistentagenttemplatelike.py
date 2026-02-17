from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0289_agent_compute_session_pull_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PersistentAgentTemplateLike',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='template_likes', to='api.persistentagenttemplate')),
                ('user', models.ForeignKey(help_text='Authenticated user that liked the template.', on_delete=django.db.models.deletion.CASCADE, related_name='liked_persistent_agent_templates', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['template', 'user'], name='api_persist_template_2e8e2c_idx'), models.Index(fields=['-created_at'], name='api_persist_created_9fc412_idx')],
                'constraints': [models.UniqueConstraint(fields=('template', 'user'), name='unique_template_like_per_user')],
            },
        ),
    ]
