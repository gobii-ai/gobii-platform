from django.db import migrations, models
import django.db.models.deletion
import uuid


def seed_llm_defaults(apps, schema_editor):
    # Import models via historical apps registry
    LLMProvider = apps.get_model('api', 'LLMProvider')
    PersistentModelEndpoint = apps.get_model('api', 'PersistentModelEndpoint')
    PersistentTokenRange = apps.get_model('api', 'PersistentTokenRange')
    PersistentLLMTier = apps.get_model('api', 'PersistentLLMTier')
    PersistentTierEndpoint = apps.get_model('api', 'PersistentTierEndpoint')
    BrowserModelEndpoint = apps.get_model('api', 'BrowserModelEndpoint')
    BrowserLLMPolicy = apps.get_model('api', 'BrowserLLMPolicy')
    BrowserLLMTier = apps.get_model('api', 'BrowserLLMTier')
    BrowserTierEndpoint = apps.get_model('api', 'BrowserTierEndpoint')

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

    # Persistent endpoints (LiteLLM)
    pe = {}
    def add_persistent_endpoint(key, provider_key, litellm_model, temperature_override=None, supports_tool_choice=True):
        ep, _ = PersistentModelEndpoint.objects.get_or_create(
            key=key,
            defaults=dict(
                provider=providers[provider_key],
                enabled=True,
                litellm_model=litellm_model,
                temperature_override=temperature_override,
                supports_tool_choice=supports_tool_choice,
            ),
        )
        pe[key] = ep
        return ep

    add_persistent_endpoint('openai_gpt4_1', 'openai', 'openai/gpt-4.1')
    add_persistent_endpoint('openai_gpt5', 'openai', 'openai/gpt-5', temperature_override=1)
    add_persistent_endpoint('anthropic_sonnet4', 'anthropic', 'anthropic/claude-sonnet-4-20250514')
    add_persistent_endpoint('google_gemini_25_pro', 'google', 'vertex_ai/gemini-2.5-pro')
    add_persistent_endpoint('openrouter_glm_45', 'openrouter', 'openrouter/z-ai/glm-4.5')
    add_persistent_endpoint('fireworks_qwen3_235b', 'fireworks', 'fireworks_ai/accounts/fireworks/models/qwen3-235b-a22b-instruct-2507', supports_tool_choice=False)
    add_persistent_endpoint('fireworks_gpt_oss_120b', 'fireworks', 'fireworks_ai/accounts/fireworks/models/gpt-oss-120b')

    # Browser endpoints
    be = {}
    def add_browser_endpoint(key, provider_key, browser_model, browser_base_url=''):
        ep, _ = BrowserModelEndpoint.objects.get_or_create(
            key=key,
            defaults=dict(
                provider=providers[provider_key],
                enabled=True,
                browser_model=browser_model,
                browser_base_url=browser_base_url,
            ),
        )
        be[key] = ep
        return ep

    add_browser_endpoint('openai_gpt5_mini', 'openai', 'gpt-5-mini')
    add_browser_endpoint('anthropic_sonnet4', 'anthropic', 'claude-sonnet-4-20250514')
    add_browser_endpoint('google_gemini_25_pro', 'google', 'gemini-2.5-pro')
    add_browser_endpoint('openrouter_glm_45', 'openrouter', 'z-ai/glm-4.5', 'https://openrouter.ai/api/v1')
    add_browser_endpoint('fireworks_qwen3_235b', 'fireworks', 'accounts/fireworks/models/qwen3-235b-a22b-instruct-2507', 'https://api.fireworks.ai/inference/v1')

    # Browser policy and tiers
    policy, _ = BrowserLLMPolicy.objects.get_or_create(name='Default', defaults=dict(is_active=True))

    t1 = BrowserLLMTier.objects.create(policy=policy, order=1, description='Tier 1')
    BrowserTierEndpoint.objects.create(tier=t1, endpoint=be['openai_gpt5_mini'], weight=0.8)
    BrowserTierEndpoint.objects.create(tier=t1, endpoint=be['anthropic_sonnet4'], weight=0.2)

    t2 = BrowserLLMTier.objects.create(policy=policy, order=2, description='Tier 2')
    BrowserTierEndpoint.objects.create(tier=t2, endpoint=be['google_gemini_25_pro'], weight=1.0)

    t3 = BrowserLLMTier.objects.create(policy=policy, order=3, description='Tier 3')
    BrowserTierEndpoint.objects.create(tier=t3, endpoint=be['fireworks_qwen3_235b'], weight=0.5)
    BrowserTierEndpoint.objects.create(tier=t3, endpoint=be['openrouter_glm_45'], weight=0.5)

    t4 = BrowserLLMTier.objects.create(policy=policy, order=4, description='Tier 4')
    BrowserTierEndpoint.objects.create(tier=t4, endpoint=be['anthropic_sonnet4'], weight=1.0)

    # Persistent token ranges and tiers
    small = PersistentTokenRange.objects.create(name='small', min_tokens=0, max_tokens=7500)
    medium = PersistentTokenRange.objects.create(name='medium', min_tokens=7500, max_tokens=20000)
    large = PersistentTokenRange.objects.create(name='large', min_tokens=20000, max_tokens=None)

    # small tiers
    s1 = PersistentLLMTier.objects.create(token_range=small, order=1, description='Tier 1')
    PersistentTierEndpoint.objects.create(tier=s1, endpoint=pe['openai_gpt5'], weight=0.9)
    PersistentTierEndpoint.objects.create(tier=s1, endpoint=pe['google_gemini_25_pro'], weight=0.1)
    s2 = PersistentLLMTier.objects.create(token_range=small, order=2, description='Tier 2')
    PersistentTierEndpoint.objects.create(tier=s2, endpoint=pe['google_gemini_25_pro'], weight=1.0)
    s3 = PersistentLLMTier.objects.create(token_range=small, order=3, description='Tier 3')
    PersistentTierEndpoint.objects.create(tier=s3, endpoint=pe['anthropic_sonnet4'], weight=0.5)
    PersistentTierEndpoint.objects.create(tier=s3, endpoint=pe['openrouter_glm_45'], weight=0.5)

    # medium tiers
    m1 = PersistentLLMTier.objects.create(token_range=medium, order=1, description='Tier 1')
    PersistentTierEndpoint.objects.create(tier=m1, endpoint=pe['openrouter_glm_45'], weight=0.70)
    PersistentTierEndpoint.objects.create(tier=m1, endpoint=pe['google_gemini_25_pro'], weight=0.10)
    PersistentTierEndpoint.objects.create(tier=m1, endpoint=pe['openai_gpt5'], weight=0.10)
    PersistentTierEndpoint.objects.create(tier=m1, endpoint=pe['fireworks_gpt_oss_120b'], weight=0.10)
    m2 = PersistentLLMTier.objects.create(token_range=medium, order=2, description='Tier 2')
    PersistentTierEndpoint.objects.create(tier=m2, endpoint=pe['openrouter_glm_45'], weight=0.34)
    PersistentTierEndpoint.objects.create(tier=m2, endpoint=pe['openai_gpt5'], weight=0.33)
    PersistentTierEndpoint.objects.create(tier=m2, endpoint=pe['anthropic_sonnet4'], weight=0.33)
    m3 = PersistentLLMTier.objects.create(token_range=medium, order=3, description='Tier 3')
    PersistentTierEndpoint.objects.create(tier=m3, endpoint=pe['openai_gpt5'], weight=1.0)

    # large tiers
    l1 = PersistentLLMTier.objects.create(token_range=large, order=1, description='Tier 1')
    PersistentTierEndpoint.objects.create(tier=l1, endpoint=pe['openrouter_glm_45'], weight=0.70)
    PersistentTierEndpoint.objects.create(tier=l1, endpoint=pe['google_gemini_25_pro'], weight=0.10)
    PersistentTierEndpoint.objects.create(tier=l1, endpoint=pe['openai_gpt5'], weight=0.10)
    PersistentTierEndpoint.objects.create(tier=l1, endpoint=pe['fireworks_gpt_oss_120b'], weight=0.10)
    l2 = PersistentLLMTier.objects.create(token_range=large, order=2, description='Tier 2')
    PersistentTierEndpoint.objects.create(tier=l2, endpoint=pe['openai_gpt5'], weight=1.0)
    l3 = PersistentLLMTier.objects.create(token_range=large, order=3, description='Tier 3')
    PersistentTierEndpoint.objects.create(tier=l3, endpoint=pe['anthropic_sonnet4'], weight=1.0)
    l4 = PersistentLLMTier.objects.create(token_range=large, order=4, description='Tier 4')
    PersistentTierEndpoint.objects.create(tier=l4, endpoint=pe['fireworks_qwen3_235b'], weight=1.0)


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

