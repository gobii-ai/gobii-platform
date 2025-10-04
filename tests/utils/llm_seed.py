from django.apps import apps


def clear_llm_db():
    LLMProvider = apps.get_model('api', 'LLMProvider')
    PersistentModelEndpoint = apps.get_model('api', 'PersistentModelEndpoint')
    PersistentTokenRange = apps.get_model('api', 'PersistentTokenRange')
    PersistentLLMTier = apps.get_model('api', 'PersistentLLMTier')
    PersistentTierEndpoint = apps.get_model('api', 'PersistentTierEndpoint')

    PersistentTierEndpoint.objects.all().delete()
    PersistentLLMTier.objects.all().delete()
    PersistentTokenRange.objects.all().delete()
    PersistentModelEndpoint.objects.all().delete()
    LLMProvider.objects.all().delete()


def seed_persistent_basic(include_openrouter=False):
    """Seed a minimal persistent LLM config used by unit tests.

    Creates providers (anthropic, google, optionally openrouter), endpoints,
    and a single small token range with tier ordering matching common tests.
    """
    LLMProvider = apps.get_model('api', 'LLMProvider')
    PersistentModelEndpoint = apps.get_model('api', 'PersistentModelEndpoint')
    PersistentTokenRange = apps.get_model('api', 'PersistentTokenRange')
    PersistentLLMTier = apps.get_model('api', 'PersistentLLMTier')
    PersistentTierEndpoint = apps.get_model('api', 'PersistentTierEndpoint')

    clear_llm_db()

    prov_a = LLMProvider.objects.create(key='anthropic', display_name='Anthropic', enabled=True, env_var_name='ANTHROPIC_API_KEY', browser_backend='ANTHROPIC')
    prov_g = LLMProvider.objects.create(key='google', display_name='Google', enabled=True, env_var_name='GOOGLE_API_KEY', browser_backend='GOOGLE')
    if include_openrouter:
        prov_o = LLMProvider.objects.create(key='openrouter', display_name='OpenRouter', enabled=True, env_var_name='OPENROUTER_API_KEY', browser_backend='OPENAI_COMPAT')
    ep_a = PersistentModelEndpoint.objects.create(
        key='anthropic_sonnet4',
        provider=prov_a,
        enabled=True,
        litellm_model='anthropic/claude-sonnet-4-20250514',
        supports_vision=True,
    )
    ep_g = PersistentModelEndpoint.objects.create(
        key='google_gemini_25_pro',
        provider=prov_g,
        enabled=True,
        litellm_model='vertex_ai/gemini-2.5-pro',
        supports_vision=True,
    )
    ep_o = None
    if include_openrouter:
        ep_o = PersistentModelEndpoint.objects.create(
            key='openrouter_glm_45',
            provider=prov_o,
            enabled=True,
            litellm_model='openrouter/z-ai/glm-4.5',
            supports_vision=False,
        )

    small = PersistentTokenRange.objects.create(name='small', min_tokens=0, max_tokens=7500)
    t1 = PersistentLLMTier.objects.create(token_range=small, order=1)
    # Default: prefer anthropic, then google
    PersistentTierEndpoint.objects.create(tier=t1, endpoint=ep_a, weight=0.75)
    PersistentTierEndpoint.objects.create(tier=t1, endpoint=ep_g, weight=0.25)
    if include_openrouter and ep_o:
        t2 = PersistentLLMTier.objects.create(token_range=small, order=2)
        PersistentTierEndpoint.objects.create(tier=t2, endpoint=ep_o, weight=1.0)

    # Medium range: mix openrouter+google+anthropic
    medium = PersistentTokenRange.objects.create(name='medium', min_tokens=7500, max_tokens=20000)
    m1 = PersistentLLMTier.objects.create(token_range=medium, order=1)
    if include_openrouter and ep_o:
        PersistentTierEndpoint.objects.create(tier=m1, endpoint=ep_o, weight=0.70)
    PersistentTierEndpoint.objects.create(tier=m1, endpoint=ep_g, weight=0.10)
    PersistentTierEndpoint.objects.create(tier=m1, endpoint=ep_a, weight=0.20)
    m2 = PersistentLLMTier.objects.create(token_range=medium, order=2)
    PersistentTierEndpoint.objects.create(tier=m2, endpoint=ep_a, weight=1.0)

    # Large range: prefer openrouter+google; fallback anthropic
    large = PersistentTokenRange.objects.create(name='large', min_tokens=20000, max_tokens=None)
    l1 = PersistentLLMTier.objects.create(token_range=large, order=1)
    if include_openrouter and ep_o:
        PersistentTierEndpoint.objects.create(tier=l1, endpoint=ep_o, weight=0.70)
    PersistentTierEndpoint.objects.create(tier=l1, endpoint=ep_g, weight=0.30)
    l2 = PersistentLLMTier.objects.create(token_range=large, order=2)
    PersistentTierEndpoint.objects.create(tier=l2, endpoint=ep_a, weight=1.0)
