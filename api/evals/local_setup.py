"""Local setup helpers for canonical eval runs."""
from dataclasses import dataclass
import os
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connection


def ensure_eval_local_database(stdout=None) -> bool:
    """Create/sync the local SQLite schema when eval-local settings request it."""
    if not settings.EVAL_LOCAL_AUTO_MIGRATE:
        return False

    db_name = settings.DATABASES["default"]["NAME"]
    if db_name and db_name != ":memory:":
        Path(db_name).parent.mkdir(parents=True, exist_ok=True)

    call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
    ensure_eval_local_compat_columns(stdout=stdout)
    if stdout:
        stdout.write("Local eval SQLite schema is ready.")
    return True


def ensure_eval_local_compat_columns(stdout=None) -> int:
    """
    Backfill columns that eval-local SQLite databases may miss when migrations are disabled.

    config.eval_local_settings uses run_syncdb for fast local setup. That creates new
    tables, but it does not alter existing SQLite tables when model fields are added.
    Keep this list explicit so local schema repair stays non-destructive.
    """
    from api.models import EvalRunTask

    compat_fields = ((EvalRunTask, ("debug_artifacts",)),)
    existing_tables = set(connection.introspection.table_names())
    missing_by_model = []
    added = 0

    with connection.cursor() as cursor:
        for model, field_names in compat_fields:
            table_name = model._meta.db_table
            if table_name not in existing_tables:
                continue

            existing_columns = {
                column.name
                for column in connection.introspection.get_table_description(cursor, table_name)
            }
            missing_fields = [
                model._meta.get_field(field_name)
                for field_name in field_names
                if model._meta.get_field(field_name).column not in existing_columns
            ]
            if not missing_fields:
                continue
            missing_by_model.append((model, table_name, missing_fields))

    for model, table_name, missing_fields in missing_by_model:
        with connection.schema_editor() as schema_editor:
            for field in missing_fields:
                schema_editor.add_field(model, field)
                added += 1
                if stdout:
                    stdout.write(
                        f"Added missing local eval column {table_name}.{field.column}."
                    )

    return added


@dataclass(frozen=True)
class EvalLocalRoutingProfileSeed:
    profile_name: str
    display_name: str
    description: str
    provider_key: str
    provider_display_name: str
    provider_env_var_name: str
    provider_model_prefix: str
    provider_browser_backend: str
    endpoint_key: str
    litellm_model: str
    api_base: str = ""


def get_eval_local_routing_profile_seeds() -> tuple[EvalLocalRoutingProfileSeed, ...]:
    seeds = [
        EvalLocalRoutingProfileSeed(
            profile_name=settings.EVAL_LOCAL_OPENROUTER_PROFILE_NAME,
            display_name="OpenRouter DeepSeek V4 Flash",
            description=(
                "Local eval profile seeded by run_evals for DeepSeek V4 Flash through OpenRouter."
            ),
            provider_key="openrouter",
            provider_display_name="OpenRouter",
            provider_env_var_name="OPENROUTER_API_KEY",
            provider_model_prefix="openrouter/",
            provider_browser_backend="OPENAI_COMPAT",
            endpoint_key=settings.EVAL_LOCAL_OPENROUTER_ENDPOINT_KEY,
            litellm_model=settings.EVAL_LOCAL_OPENROUTER_MODEL,
        ),
        EvalLocalRoutingProfileSeed(
            profile_name=settings.EVAL_LOCAL_OPENROUTER_QWEN_PROFILE_NAME,
            display_name="OpenRouter Qwen",
            description="Local eval profile seeded by run_evals for Qwen through OpenRouter.",
            provider_key="openrouter",
            provider_display_name="OpenRouter",
            provider_env_var_name="OPENROUTER_API_KEY",
            provider_model_prefix="openrouter/",
            provider_browser_backend="OPENAI_COMPAT",
            endpoint_key=settings.EVAL_LOCAL_OPENROUTER_QWEN_ENDPOINT_KEY,
            litellm_model=settings.EVAL_LOCAL_OPENROUTER_QWEN_MODEL,
        ),
        EvalLocalRoutingProfileSeed(
            profile_name=settings.EVAL_LOCAL_OPENAI_PROFILE_NAME,
            display_name="OpenAI GPT-4.1 Mini",
            description="Local eval profile seeded by run_evals for OpenAI tool-calling checks.",
            provider_key="openai",
            provider_display_name="OpenAI",
            provider_env_var_name="OPENAI_API_KEY",
            provider_model_prefix="",
            provider_browser_backend="OPENAI",
            endpoint_key=settings.EVAL_LOCAL_OPENAI_ENDPOINT_KEY,
            litellm_model=settings.EVAL_LOCAL_OPENAI_MODEL,
        ),
    ]

    custom_model = settings.EVAL_LOCAL_CUSTOM_MODEL.strip()
    if custom_model:
        seeds.append(
            EvalLocalRoutingProfileSeed(
                profile_name=settings.EVAL_LOCAL_CUSTOM_PROFILE_NAME,
                display_name=settings.EVAL_LOCAL_CUSTOM_PROVIDER_DISPLAY_NAME,
                description=(
                    "Local eval profile seeded by run_evals from EVAL_LOCAL_CUSTOM_MODEL."
                ),
                provider_key=settings.EVAL_LOCAL_CUSTOM_PROVIDER_KEY,
                provider_display_name=settings.EVAL_LOCAL_CUSTOM_PROVIDER_DISPLAY_NAME,
                provider_env_var_name=settings.EVAL_LOCAL_CUSTOM_API_KEY_ENV_VAR,
                provider_model_prefix="",
                provider_browser_backend="OPENAI_COMPAT",
                endpoint_key=settings.EVAL_LOCAL_CUSTOM_ENDPOINT_KEY,
                litellm_model=custom_model,
                api_base=settings.EVAL_LOCAL_CUSTOM_API_BASE.strip(),
            )
        )

    return tuple(seeds)


def ensure_eval_local_routing_profile(seed: EvalLocalRoutingProfileSeed, stdout=None):
    """
    Seed one minimal routing profile for local evals.

    The profile stores the provider env var name only. It never reads, stores,
    or prints the raw key value.
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
        key=seed.provider_key,
        defaults={
            "display_name": seed.provider_display_name,
            "enabled": True,
            "env_var_name": seed.provider_env_var_name,
            "model_prefix": seed.provider_model_prefix,
            "browser_backend": seed.provider_browser_backend,
        },
    )

    endpoint, _ = PersistentModelEndpoint.objects.update_or_create(
        key=seed.endpoint_key,
        defaults={
            "provider": provider,
            "enabled": True,
            "low_latency": True,
            "litellm_model": seed.litellm_model,
            "temperature_override": 0.0,
            "supports_temperature": True,
            "supports_tool_choice": True,
            "use_parallel_tool_calls": False,
            "allow_implied_send": False,
            "supports_vision": False,
            "supports_reasoning": False,
            "api_base": seed.api_base,
        },
    )

    profile, _ = LLMRoutingProfile.objects.update_or_create(
        name=seed.profile_name,
        defaults={
            "display_name": seed.display_name,
            "description": (
                f"{seed.description} Uses {seed.provider_env_var_name} from the environment at runtime."
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
        key_status = "present" if os.environ.get(seed.provider_env_var_name) else "not set"
        stdout.write(
            "Seeded local eval routing profile "
            f"'{profile.name}' for {seed.litellm_model} using env var "
            f"{seed.provider_env_var_name} ({key_status})."
        )

    return profile


def ensure_openrouter_deepseek_v4_flash_profile(stdout=None):
    seed = get_eval_local_routing_profile_seeds()[0]
    return ensure_eval_local_routing_profile(seed, stdout=stdout)


def ensure_eval_local_routing_profiles(stdout=None):
    profiles = []
    for seed in get_eval_local_routing_profile_seeds():
        profiles.append(ensure_eval_local_routing_profile(seed, stdout=stdout))
    return profiles


def ensure_eval_local_setup(stdout=None):
    ensure_eval_local_database(stdout=stdout)
    return ensure_eval_local_routing_profiles(stdout=stdout)
