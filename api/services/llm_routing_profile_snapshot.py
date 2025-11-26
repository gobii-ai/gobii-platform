"""
Utility for creating deep clones of LLMRoutingProfile for eval snapshots.

When an eval suite run is created with an LLM routing profile, we clone the entire
profile hierarchy so the eval has an immutable record of the exact configuration used.
"""
import uuid
import logging
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def create_eval_profile_snapshot(source_profile, suite_run_id: str):
    """
    Create a deep clone of an LLMRoutingProfile for an eval suite run.

    The cloned profile:
    - Has is_eval_snapshot=True (immutable, won't appear in normal profile lists)
    - Has cloned_from pointing to the source profile
    - Has a unique name based on source name and suite run ID
    - Copies all token ranges, tiers, and tier endpoints

    Args:
        source_profile: The LLMRoutingProfile to clone
        suite_run_id: The ID of the EvalSuiteRun (used to generate unique name)

    Returns:
        The cloned LLMRoutingProfile instance
    """
    from api.models import (
        LLMRoutingProfile,
        ProfileTokenRange,
        ProfilePersistentTier,
        ProfilePersistentTierEndpoint,
        ProfileBrowserTier,
        ProfileBrowserTierEndpoint,
        ProfileEmbeddingsTier,
        ProfileEmbeddingsTierEndpoint,
    )

    if source_profile is None:
        return None

    # Generate a unique name for the snapshot
    short_id = str(suite_run_id)[:8]
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
    snapshot_name = f"{source_profile.name}-eval-{short_id}-{timestamp}"

    # Ensure name uniqueness (truncate if needed, max 64 chars)
    if len(snapshot_name) > 64:
        snapshot_name = snapshot_name[:64]

    with transaction.atomic():
        # Clone the profile
        cloned_profile = LLMRoutingProfile.objects.create(
            name=snapshot_name,
            display_name=f"{source_profile.display_name} (Eval Snapshot)",
            description=f"Snapshot of '{source_profile.name}' for eval suite run {suite_run_id}",
            is_active=False,
            is_eval_snapshot=True,
            cloned_from=source_profile,
            created_by=source_profile.created_by,
            eval_judge_endpoint=source_profile.eval_judge_endpoint,
        )

        # Clone token ranges and their tiers
        for token_range in source_profile.persistent_token_ranges.all():
            cloned_range = ProfileTokenRange.objects.create(
                profile=cloned_profile,
                name=token_range.name,
                min_tokens=token_range.min_tokens,
                max_tokens=token_range.max_tokens,
            )

            # Clone tiers for this range
            for tier in token_range.tiers.all():
                cloned_tier = ProfilePersistentTier.objects.create(
                    token_range=cloned_range,
                    order=tier.order,
                    description=tier.description,
                    is_premium=tier.is_premium,
                    is_max=tier.is_max,
                    credit_multiplier=tier.credit_multiplier,
                )

                # Clone tier endpoints
                for tier_endpoint in tier.tier_endpoints.all():
                    ProfilePersistentTierEndpoint.objects.create(
                        tier=cloned_tier,
                        endpoint=tier_endpoint.endpoint,
                        weight=tier_endpoint.weight,
                    )

        # Clone browser tiers
        for browser_tier in source_profile.browser_tiers.all():
            cloned_browser_tier = ProfileBrowserTier.objects.create(
                profile=cloned_profile,
                order=browser_tier.order,
                description=browser_tier.description,
                is_premium=browser_tier.is_premium,
            )

            # Clone browser tier endpoints
            for tier_endpoint in browser_tier.tier_endpoints.all():
                ProfileBrowserTierEndpoint.objects.create(
                    tier=cloned_browser_tier,
                    endpoint=tier_endpoint.endpoint,
                    weight=tier_endpoint.weight,
                )

        # Clone embeddings tiers
        for embeddings_tier in source_profile.embeddings_tiers.all():
            cloned_embeddings_tier = ProfileEmbeddingsTier.objects.create(
                profile=cloned_profile,
                order=embeddings_tier.order,
                description=embeddings_tier.description,
            )

            # Clone embeddings tier endpoints
            for tier_endpoint in embeddings_tier.tier_endpoints.all():
                ProfileEmbeddingsTierEndpoint.objects.create(
                    tier=cloned_embeddings_tier,
                    endpoint=tier_endpoint.endpoint,
                    weight=tier_endpoint.weight,
                )

        logger.info(
            "Created eval profile snapshot: %s (from: %s) for suite run %s",
            cloned_profile.name,
            source_profile.name,
            suite_run_id,
        )

        return cloned_profile
