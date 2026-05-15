"""Local setup helpers for canonical eval runs."""
import os
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command


def ensure_eval_local_database(stdout=None) -> bool:
    """Create/sync the local SQLite schema when eval-local settings request it."""
    if not settings.EVAL_LOCAL_AUTO_MIGRATE:
        return False

    db_name = settings.DATABASES["default"]["NAME"]
    if db_name and db_name != ":memory:":
        Path(db_name).parent.mkdir(parents=True, exist_ok=True)

    call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
    if stdout:
        stdout.write("Local eval SQLite schema is ready.")
    return True


def ensure_openrouter_deepseek_v4_flash_profile(stdout=None):
    """
    Seed the minimal routing profile used by local live Meta Gobii evals.

    The profile stores the provider's env var name only. It never reads, stores,
    or prints the raw OPENROUTER_API_KEY value.
    """
    from api.models import (
        IntelligenceTier,
        LLMProvider,
        LLMRoutingProfile,
        PersistentModelEndpoint,
        ProfilePersistentTier,
        ProfilePersistentTierEndpoint,
        ProfileTokenRange,
    )

    standard_tier, _ = IntelligenceTier.objects.update_or_create(
        key="standard",
        defaults={
            "display_name": "Standard",
            "rank": 0,
            "credit_multiplier": Decimal("1.00"),
        },
    )

    provider, _ = LLMProvider.objects.update_or_create(
        key="openrouter",
        defaults={
            "display_name": "OpenRouter",
            "enabled": True,
            "env_var_name": "OPENROUTER_API_KEY",
            "model_prefix": "openrouter/",
            "browser_backend": LLMProvider.BrowserBackend.OPENAI_COMPAT,
        },
    )

    endpoint, _ = PersistentModelEndpoint.objects.update_or_create(
        key=settings.EVAL_LOCAL_OPENROUTER_ENDPOINT_KEY,
        defaults={
            "provider": provider,
            "enabled": True,
            "low_latency": True,
            "litellm_model": settings.EVAL_LOCAL_OPENROUTER_MODEL,
            "temperature_override": 0.0,
            "supports_temperature": True,
            "supports_tool_choice": True,
            "use_parallel_tool_calls": False,
            "allow_implied_send": False,
            "supports_vision": False,
            "supports_reasoning": False,
        },
    )

    profile, _ = LLMRoutingProfile.objects.update_or_create(
        name=settings.EVAL_LOCAL_OPENROUTER_PROFILE_NAME,
        defaults={
            "display_name": "OpenRouter DeepSeek V4 Flash",
            "description": (
                "Local eval profile seeded by run_evals. Uses OPENROUTER_API_KEY "
                "from the environment at runtime."
            ),
            "is_active": False,
            "is_eval_snapshot": False,
            "eval_judge_endpoint": endpoint,
            "summarization_endpoint": endpoint,
            "agent_judge_endpoint": endpoint,
        },
    )

    token_range, _ = ProfileTokenRange.objects.update_or_create(
        profile=profile,
        name="default",
        defaults={"min_tokens": 0, "max_tokens": None},
    )
    tier, _ = ProfilePersistentTier.objects.update_or_create(
        token_range=token_range,
        order=1,
        intelligence_tier=standard_tier,
        defaults={"description": "Local eval primary tier"},
    )
    ProfilePersistentTierEndpoint.objects.update_or_create(
        tier=tier,
        endpoint=endpoint,
        defaults={"weight": 1.0},
    )
    ProfilePersistentTierEndpoint.objects.filter(tier=tier).exclude(endpoint=endpoint).delete()

    if stdout:
        key_status = "present" if os.environ.get("OPENROUTER_API_KEY") else "not set"
        stdout.write(
            "Seeded local eval routing profile "
            f"'{profile.name}' using env var OPENROUTER_API_KEY ({key_status})."
        )

    return profile


def ensure_eval_local_setup(stdout=None):
    ensure_eval_local_database(stdout=stdout)
    return ensure_openrouter_deepseek_v4_flash_profile(stdout=stdout)

