from django.db import migrations, models
import django.db.models.deletion
import uuid


def seed_llm_defaults(apps, schema_editor):
    # Import models via historical apps registry
    LLMProvider = apps.get_model('api', 'LLMProvider')

    # Create providers (enabled by default; no admin keys seeded)
    providers = {}
    def ensure_provider(key, display_name, env_var_name, browser_backend, supports_safety_identifier=False, vertex_project='', vertex_location=''):
        prov, _ = LLMProvider.objects.get_or_create(
            key=key,
            defaults=dict(
                display_name=display_name,
                enabled=True,
                env_var_name=env_var_name,
                browser_backend=browser_backend,
                supports_safety_identifier=supports_safety_identifier,
                vertex_project=vertex_project,
                vertex_location=vertex_location,
            ),
        )
        providers[key] = prov
        return prov

    ensure_provider('openai', 'OpenAI', 'OPENAI_API_KEY', 'OPENAI', supports_safety_identifier=True)
    ensure_provider('anthropic', 'Anthropic', 'ANTHROPIC_API_KEY', 'ANTHROPIC')
    ensure_provider('google', 'Google Vertex AI', 'GOOGLE_API_KEY', 'GOOGLE', vertex_project='browser-use-458714', vertex_location='us-east4')
    ensure_provider('openrouter', 'OpenRouter', 'OPENROUTER_API_KEY', 'OPENAI_COMPAT')
    ensure_provider('fireworks', 'Fireworks', 'FIREWORKS_AI_API_KEY', 'OPENAI_COMPAT')


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0120_userquota_max_agent_contacts'),
    ]

    operations = [
        migrations.CreateModel(
            name='LLMProvider',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('key', models.SlugField(max_length=64, unique=True)),
                ('display_name', models.CharField(max_length=128)),
                ('enabled', models.BooleanField(default=True)),
                ('api_key_encrypted', models.BinaryField(blank=True, null=True)),
                ('env_var_name', models.CharField(blank=True, max_length=128)),
                ('supports_safety_identifier', models.BooleanField(default=False)),
                ('browser_backend', models.CharField(choices=[('OPENAI', 'OpenAI'), ('ANTHROPIC', 'Anthropic'), ('GOOGLE', 'Google'), ('OPENAI_COMPAT', 'Openai-Compatible')], default='OPENAI', max_length=16)),
                ('vertex_project', models.CharField(blank=True, max_length=128)),
                ('vertex_location', models.CharField(blank=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['display_name']},
        ),
        migrations.CreateModel(
            name='PersistentModelEndpoint',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('key', models.SlugField(max_length=96, unique=True)),
                ('enabled', models.BooleanField(default=True)),
                ('litellm_model', models.CharField(max_length=256)),
                ('temperature_override', models.FloatField(blank=True, null=True)),
                ('supports_tool_choice', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='persistent_endpoints', to='api.llmprovider')),
            ],
            options={'ordering': ['provider__display_name', 'litellm_model']},
        ),
        migrations.CreateModel(
            name='PersistentTokenRange',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('name', models.CharField(max_length=64, unique=True)),
                ('min_tokens', models.PositiveIntegerField()),
                ('max_tokens', models.PositiveIntegerField(blank=True, null=True)),
            ],
            options={'ordering': ['min_tokens']},
        ),
        migrations.CreateModel(
            name='PersistentLLMTier',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('order', models.PositiveIntegerField(help_text='1-based order within the range')),
                ('description', models.CharField(blank=True, max_length=256)),
                ('token_range', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tiers', to='api.persistenttokenrange')),
            ],
            options={'ordering': ['token_range__min_tokens', 'order']},
        ),
        migrations.CreateModel(
            name='PersistentTierEndpoint',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('weight', models.FloatField(help_text='Relative weight within the tier; > 0')),
                ('endpoint', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='in_tiers', to='api.persistentmodelendpoint')),
                ('tier', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tier_endpoints', to='api.persistentllmtier')),
            ],
            options={'ordering': ['tier__order', 'endpoint__key']},
        ),
        migrations.CreateModel(
            name='BrowserModelEndpoint',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('key', models.SlugField(max_length=96, unique=True)),
                ('enabled', models.BooleanField(default=True)),
                ('browser_model', models.CharField(max_length=256)),
                ('browser_base_url', models.CharField(blank=True, max_length=256)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='browser_endpoints', to='api.llmprovider')),
            ],
            options={'ordering': ['provider__display_name', 'browser_model']},
        ),
        migrations.CreateModel(
            name='BrowserLLMPolicy',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('name', models.CharField(max_length=128, unique=True)),
                ('is_active', models.BooleanField(default=False)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='BrowserLLMTier',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('order', models.PositiveIntegerField(help_text='1-based order within the policy')),
                ('description', models.CharField(blank=True, max_length=256)),
                ('policy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tiers', to='api.browserllmpolicy')),
            ],
            options={'ordering': ['policy__name', 'order']},
        ),
        migrations.CreateModel(
            name='BrowserTierEndpoint',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('weight', models.FloatField(help_text='Relative weight within the tier; > 0')),
                ('endpoint', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='in_tiers', to='api.browsermodelendpoint')),
                ('tier', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tier_endpoints', to='api.browserllmtier')),
            ],
            options={'ordering': ['tier__order', 'endpoint__key']},
        ),
        migrations.AddConstraint(
            model_name='persistentllmtier',
            constraint=models.UniqueConstraint(fields=('token_range', 'order'), name='uniq_persistent_tier_order_per_range'),
        ),
        migrations.AddConstraint(
            model_name='persistenttierendpoint',
            constraint=models.UniqueConstraint(fields=('tier', 'endpoint'), name='uniq_persistent_endpoint_per_tier'),
        ),
        migrations.AddConstraint(
            model_name='browserllmtier',
            constraint=models.UniqueConstraint(fields=('policy', 'order'), name='uniq_browser_tier_order_per_policy'),
        ),
        migrations.AddConstraint(
            model_name='browsertierendpoint',
            constraint=models.UniqueConstraint(fields=('tier', 'endpoint'), name='uniq_browser_endpoint_per_tier'),
        ),
        migrations.RunPython(seed_llm_defaults, reverse_code=migrations.RunPython.noop),
    ]

