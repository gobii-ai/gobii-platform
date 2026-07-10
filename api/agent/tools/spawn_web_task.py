"""
Web task spawning tool for persistent agents.

This module provides web automation task spawning functionality for persistent agents,
including tool definition and execution logic.
"""

import logging
from typing import Dict, Any, Optional

from django.conf import settings
from django.utils import timezone

from ...models import PersistentAgent, BrowserUseAgentTask, BrowserUseAgentTaskStep, GlobalSecret, PersistentAgentSecret
from ..core.budget import get_current_context as get_budget_context, AgentBudgetManager
from ...services.persistent_agent_secrets import build_browser_task_secret_payload
from ...services.billing_snapshot import get_billing_snapshot_for_owner
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from ...services.browser_settings import get_browser_settings_for_owner

logger = logging.getLogger(__name__)


def _should_simulate_eval_weather_task(prompt: str, eval_run_id: str | None) -> bool:
    prompt_lower = (prompt or "").lower()
    return (
        settings.BROWSER_USE_TASK_EXECUTION_DISABLED
        and settings.EVAL_BROWSER_TASK_SIMULATION_ENABLED
        and bool(eval_run_id)
        and "eval/sim/weather" in prompt_lower
        and "pollution" in prompt_lower
    )


def _create_simulated_eval_weather_task(
    agent: PersistentAgent,
    *,
    prompt: str,
    requires_vision: bool,
    eval_run_id: str,
) -> dict[str, Any]:
    result_value = {
        "content": "Washington DC pollution index: Moderate (55).",
        "pollution_index": 55,
        "pollution_status": "Moderate",
    }
    task = BrowserUseAgentTask.objects.create(
        agent=agent.browser_use_agent,
        user=agent.user,
        prompt=prompt,
        requires_vision=requires_vision,
        eval_run_id=eval_run_id,
        status=BrowserUseAgentTask.StatusChoices.COMPLETED,
        **get_billing_snapshot_for_owner(_get_plan_owner(agent)),
    )
    BrowserUseAgentTaskStep.objects.create(
        task=task,
        step_number=1,
        description="Eval-local simulated browser result for SimWeather pollution page.",
        is_result=True,
        result_value=result_value,
    )
    return {
        "status": "completed",
        "task_id": str(task.id),
        "message": "Browser task completed with eval-local simulated result.",
        "result": result_value,
    }


def _get_plan_owner(agent: Optional[PersistentAgent]):
    if agent is None:
        return None
    return agent.organization or agent.user


def _get_browser_settings(agent: Optional[PersistentAgent]):
    return get_browser_settings_for_owner(_get_plan_owner(agent))


def get_browser_daily_task_limit(agent: Optional[PersistentAgent] = None) -> Optional[int]:
    """Return the configured daily browser task limit or None when unlimited."""
    return _get_browser_settings(agent).max_browser_tasks


def get_spawn_web_task_tool(agent: Optional[PersistentAgent] = None) -> Dict[str, Any]:
    """Return the spawn_web_task tool definition for the LLM."""
    settings = _get_browser_settings(agent)
    max_tasks = settings.max_active_browser_tasks
    daily_limit = settings.max_browser_tasks
    limit_bits = []
    if max_tasks:
        limit_bits.append(f"Maximum {max_tasks} active tasks at once.")
    if daily_limit:
        limit_bits.append(f"Maximum {daily_limit} browser tasks per day.")
    step_limit = settings.max_browser_steps
    if step_limit:
        limit_bits.append(f"Maximum {step_limit} steps per browser task.")
    if not limit_bits:
        limit_bits.append("Task limits enforced per deployment settings.")
    limit_sentence = " ".join(limit_bits)

    return {
        "type": "function",
        "function": {
            "name": "spawn_web_task",
            "description": (
                "Async browser for interactive/authenticated/rendered pages, screenshots, visual capture, PDFs, and page artifacts. Prefer direct API/structured/text tools for accessible text-only pages. "
                "Give concrete URLs and actions; name stored login secrets directly, while OAuth uses integration auth. Completion wakes you—do not poll. "
                f"{limit_sentence}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Task prompt."},
                    "requires_vision": {
                        "type": "boolean",
                        "description": (
                            "True only when the browser must interpret visual content; capture alone does not require vision."
                        ),
                    },
                    "secrets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of secret keys to provide to the web task. If not specified, all available secrets will be provided.",
                    },
                },
                "required": ["prompt"],
            },
        },
    }


def execute_spawn_web_task(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the spawn_web_task tool for a persistent agent."""
    from ...tasks.browser_agent_tasks import process_browser_use_task

    prompt = params.get("prompt")
    if not prompt:
        return {"status": "error", "message": "Missing required parameter: prompt"}

    requires_vision = bool(params.get("requires_vision", False))

    # Get optional secrets parameter
    requested_secrets = params.get("secrets", [])
    
    browser_use_agent = agent.browser_use_agent

    plan_settings = _get_browser_settings(agent)
    budget_ctx = get_budget_context()
    eval_run_id = getattr(budget_ctx, "eval_run_id", None)

    # Check active task limit from settings (per agent)
    active_count = BrowserUseAgentTask.objects.filter(
        agent=browser_use_agent,
        status__in=[
            BrowserUseAgentTask.StatusChoices.PENDING,
            BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
        ]
    ).count()

    max_tasks = plan_settings.max_active_browser_tasks
    if max_tasks and active_count >= max_tasks:
        return {
            "status": "error", 
            "message": f"Maximum active task limit reached ({max_tasks}). Currently have {active_count} active tasks."
        }

    # Check daily task creation limit
    daily_limit = plan_settings.max_browser_tasks
    if daily_limit:
        start_of_day = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = BrowserUseAgentTask.objects.filter(
            agent=browser_use_agent,
            created_at__gte=start_of_day,
        ).count()
        if daily_count >= daily_limit:
            props = Analytics.with_org_properties(
                {
                    "agent_id": str(agent.id),
                    "browser_agent_id": str(browser_use_agent.id) if browser_use_agent else None,
                    "daily_limit": daily_limit,
                    "tasks_started_today": daily_count,
                },
                organization_id=str(agent.organization_id) if getattr(agent, "organization_id", None) else None,
            )
            try:
                Analytics.track_event(
                    agent.user_id,
                    AnalyticsEvent.PERSISTENT_AGENT_BROWSER_DAILY_LIMIT_REACHED,
                    AnalyticsSource.AGENT,
                    props,
                )
            except Exception:
                logger.debug("Failed to emit analytics for browser daily limit", exc_info=True)
            return {
                "status": "error",
                "message": (
                    f"Daily browser task limit reached ({daily_limit}). "
                    f"You have already started {daily_count} task(s) today."
                ),
            }

    if _should_simulate_eval_weather_task(prompt, eval_run_id):
        return _create_simulated_eval_weather_task(
            agent,
            prompt=prompt,
            requires_vision=requires_vision,
            eval_run_id=eval_run_id,
        )
    
    # Log web task creation
    prompt_preview = prompt[:200] + "..." if len(prompt) > 200 else prompt
    logger.info(
        "Agent %s spawning web task: %s%s",
        agent.id, prompt_preview,
        f" (with secrets: {', '.join(requested_secrets)})" if requested_secrets else ""
    )

    try:
        # ---------------- Recursion gating ---------------- #
        next_depth = 1
        budget_id = None
        branch_id = None
        if budget_ctx is not None:
            budget_id = budget_ctx.budget_id
            branch_id = budget_ctx.branch_id
            # Use the current depth from context (don't read from Redis to avoid race conditions)
            current_depth = int(getattr(budget_ctx, "depth", 0))
            # Get the max depth limit
            _, max_depth = AgentBudgetManager.get_limits(agent_id=str(agent.id))
            if current_depth >= max_depth:
                return {
                    "status": "error",
                    "message": "Recursion limit reached; cannot spawn additional background web tasks.",
                }
            # Simply calculate the next depth without mutating shared state
            next_depth = current_depth + 1

        # Merge global + agent credential secrets (agent-level wins on key+domain conflict)
        from django.db.models import Q
        global_filter = (
            Q(organization=agent.organization) if agent.organization_id
            else Q(user=agent.user, organization__isnull=True)
        )
        global_credential_secrets = list(
            GlobalSecret.objects.filter(
                global_filter,
                secret_type=GlobalSecret.SecretType.CREDENTIAL,
            )
        )
        agent_secrets_qs = agent.secrets.filter(
            requested=False,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
        )

        # Build a combined list: global first, agent overrides
        agent_secret_keys = set()
        agent_secrets_list = list(agent_secrets_qs)
        for s in agent_secrets_list:
            agent_secret_keys.add((s.domain_pattern, s.key))

        combined_secrets = [
            s for s in global_credential_secrets
            if (s.domain_pattern, s.key) not in agent_secret_keys
        ] + agent_secrets_list

        # Filter secrets if specific ones were requested
        if requested_secrets:
            available_secret_keys = {s.key for s in combined_secrets}
            missing_secrets = set(requested_secrets) - available_secret_keys

            if missing_secrets:
                return {
                    "status": "error",
                    "message": f"Requested secret keys not found: {', '.join(sorted(missing_secrets))}. Available secret keys: {', '.join(sorted(available_secret_keys)) if available_secret_keys else 'none'}"
                }

            combined_secrets = [s for s in combined_secrets if s.key in requested_secrets]

        encrypted_secrets, secret_keys_by_domain, invalid_secrets = build_browser_task_secret_payload(
            agent,
            combined_secrets,
        )

        if invalid_secrets:
            logger.warning(
                "Persistent agent secrets are invalid for browser task injection",
                extra={
                    "agent_id": str(agent.id),
                    "invalid_secret_count": len(invalid_secrets),
                    "invalid_secret_ids": [entry["id"] for entry in invalid_secrets],
                    "invalid_secret_keys": [entry["key"] for entry in invalid_secrets],
                    "invalid_secret_domains": [entry["domain_pattern"] for entry in invalid_secrets],
                },
            )

        if invalid_secrets and requested_secrets:
            invalid_details = ", ".join(
                f"{entry['key']} ({entry['domain_pattern']}): {entry['error']}"
                for entry in invalid_secrets
            )
            return {
                "status": "error",
                "message": (
                    "Requested secret keys are stored with invalid configuration and cannot be used: "
                    f"{invalid_details}"
                ),
            }

        task = BrowserUseAgentTask.objects.create(
            agent=browser_use_agent,
            user=agent.user,
            prompt=prompt,
            requires_vision=requires_vision,
            eval_run_id=getattr(budget_ctx, "eval_run_id", None),
            encrypted_secrets=encrypted_secrets,
            secret_keys=secret_keys_by_domain,
            **get_billing_snapshot_for_owner(_get_plan_owner(agent)),
        )

        # If we have a parent branch, increment its outstanding-children counter
        try:
            if branch_id and budget_id:
                AgentBudgetManager.bump_branch_depth(
                    agent_id=str(agent.id), branch_id=str(branch_id), delta=+1
                )
                logger.info(
                    "Incremented outstanding children for agent %s branch %s",
                    agent.id,
                    branch_id,
                )
        except Exception:
            logger.warning(
                "Failed to increment outstanding children for agent %s branch %s",
                agent.id,
                branch_id,
                exc_info=True,
            )

        # Spawn the browser task asynchronously via Celery, propagating budget context

        process_browser_use_task.delay(
            str(task.id),
            persistent_agent_id=agent.id,
            budget_id=budget_id,
            branch_id=branch_id,
            depth=next_depth,
        )
        
        return {
            "status": "pending",
            "task_id": str(task.id),
            "auto_sleep_ok": True,
        }

    except Exception as e:
        logger.exception(
            "Failed to create or execute BrowserUseAgentTask for agent %s", agent.id
        )
        return {"status": "error", "message": f"Failed to create or execute task: {e}"}
