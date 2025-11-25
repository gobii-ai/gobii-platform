"""Data migration to seed a default LLMRoutingProfile from existing config.

This migration:
1. Creates a 'default' LLMRoutingProfile (is_active=True)
2. Copies all PersistentTokenRange → ProfileTokenRange
3. Copies all PersistentLLMTier → ProfilePersistentTier
4. Copies all PersistentTierEndpoint → ProfilePersistentTierEndpoint
5. Copies all BrowserLLMTier (from active policy) → ProfileBrowserTier
6. Copies all BrowserTierEndpoint → ProfileBrowserTierEndpoint
7. Copies all EmbeddingsLLMTier → ProfileEmbeddingsTier
8. Copies all EmbeddingsTierEndpoint → ProfileEmbeddingsTierEndpoint
"""

from django.db import migrations


def seed_default_profile(apps, schema_editor):
    """Copy existing LLM config into a new 'default' routing profile."""

    # Get models via historical apps registry
    LLMRoutingProfile = apps.get_model('api', 'LLMRoutingProfile')
    ProfileTokenRange = apps.get_model('api', 'ProfileTokenRange')
    ProfilePersistentTier = apps.get_model('api', 'ProfilePersistentTier')
    ProfilePersistentTierEndpoint = apps.get_model('api', 'ProfilePersistentTierEndpoint')
    ProfileBrowserTier = apps.get_model('api', 'ProfileBrowserTier')
    ProfileBrowserTierEndpoint = apps.get_model('api', 'ProfileBrowserTierEndpoint')
    ProfileEmbeddingsTier = apps.get_model('api', 'ProfileEmbeddingsTier')
    ProfileEmbeddingsTierEndpoint = apps.get_model('api', 'ProfileEmbeddingsTierEndpoint')

    # Legacy models
    PersistentTokenRange = apps.get_model('api', 'PersistentTokenRange')
    PersistentLLMTier = apps.get_model('api', 'PersistentLLMTier')
    PersistentTierEndpoint = apps.get_model('api', 'PersistentTierEndpoint')
    BrowserLLMPolicy = apps.get_model('api', 'BrowserLLMPolicy')
    BrowserLLMTier = apps.get_model('api', 'BrowserLLMTier')
    BrowserTierEndpoint = apps.get_model('api', 'BrowserTierEndpoint')
    EmbeddingsLLMTier = apps.get_model('api', 'EmbeddingsLLMTier')
    EmbeddingsTierEndpoint = apps.get_model('api', 'EmbeddingsTierEndpoint')

    # Check if any legacy config exists - if not, skip seeding
    has_persistent = PersistentTokenRange.objects.exists()
    has_browser = BrowserLLMPolicy.objects.filter(is_active=True).exists()
    has_embeddings = EmbeddingsLLMTier.objects.exists()

    if not (has_persistent or has_browser or has_embeddings):
        # No legacy config to migrate - skip
        return

    # Create the default profile
    profile = LLMRoutingProfile.objects.create(
        name='default',
        display_name='Default',
        description='Auto-migrated from legacy LLM configuration.',
        is_active=True,
    )

    # --- Persistent Agent Config ---
    # Map old token range IDs to new ones
    token_range_map = {}  # old_id -> new ProfileTokenRange

    for old_range in PersistentTokenRange.objects.all():
        new_range = ProfileTokenRange.objects.create(
            profile=profile,
            name=old_range.name,
            min_tokens=old_range.min_tokens,
            max_tokens=old_range.max_tokens,
        )
        token_range_map[old_range.id] = new_range

    # Map old tier IDs to new ones
    persistent_tier_map = {}  # old_id -> new ProfilePersistentTier

    for old_tier in PersistentLLMTier.objects.all():
        new_token_range = token_range_map.get(old_tier.token_range_id)
        if not new_token_range:
            continue  # Skip orphaned tiers

        new_tier = ProfilePersistentTier.objects.create(
            token_range=new_token_range,
            order=old_tier.order,
            description=old_tier.description,
            is_premium=old_tier.is_premium,
            is_max=old_tier.is_max,
            credit_multiplier=old_tier.credit_multiplier,
        )
        persistent_tier_map[old_tier.id] = new_tier

    # Copy tier endpoints
    for old_te in PersistentTierEndpoint.objects.all():
        new_tier = persistent_tier_map.get(old_te.tier_id)
        if not new_tier:
            continue  # Skip orphaned endpoints

        ProfilePersistentTierEndpoint.objects.create(
            tier=new_tier,
            endpoint_id=old_te.endpoint_id,  # Same endpoint, just different tier reference
            weight=old_te.weight,
            is_premium=old_te.is_premium,
            is_max=old_te.is_max,
        )

    # --- Browser Agent Config ---
    active_policy = BrowserLLMPolicy.objects.filter(is_active=True).first()

    if active_policy:
        # Map old browser tier IDs to new ones
        browser_tier_map = {}  # old_id -> new ProfileBrowserTier

        for old_tier in BrowserLLMTier.objects.filter(policy=active_policy):
            new_tier = ProfileBrowserTier.objects.create(
                profile=profile,
                order=old_tier.order,
                description=old_tier.description,
                is_premium=old_tier.is_premium,
            )
            browser_tier_map[old_tier.id] = new_tier

        # Copy browser tier endpoints
        for old_te in BrowserTierEndpoint.objects.filter(tier__policy=active_policy):
            new_tier = browser_tier_map.get(old_te.tier_id)
            if not new_tier:
                continue

            ProfileBrowserTierEndpoint.objects.create(
                tier=new_tier,
                endpoint_id=old_te.endpoint_id,
                weight=old_te.weight,
                is_premium=old_te.is_premium,
            )

    # --- Embeddings Config ---
    # Map old embeddings tier IDs to new ones
    embeddings_tier_map = {}  # old_id -> new ProfileEmbeddingsTier

    for old_tier in EmbeddingsLLMTier.objects.all():
        new_tier = ProfileEmbeddingsTier.objects.create(
            profile=profile,
            order=old_tier.order,
            description=old_tier.description,
        )
        embeddings_tier_map[old_tier.id] = new_tier

    # Copy embeddings tier endpoints
    for old_te in EmbeddingsTierEndpoint.objects.all():
        new_tier = embeddings_tier_map.get(old_te.tier_id)
        if not new_tier:
            continue

        ProfileEmbeddingsTierEndpoint.objects.create(
            tier=new_tier,
            endpoint_id=old_te.endpoint_id,
            weight=old_te.weight,
        )


def reverse_seed(apps, schema_editor):
    """Remove the seeded default profile."""
    LLMRoutingProfile = apps.get_model('api', 'LLMRoutingProfile')
    LLMRoutingProfile.objects.filter(name='default').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0206_llm_routing_profiles'),
    ]

    operations = [
        migrations.RunPython(seed_default_profile, reverse_seed),
    ]
