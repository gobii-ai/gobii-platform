"""
Web task spawning tool for persistent agents.

This module provides web automation task spawning functionality for persistent agents,
including tool definition and execution logic.
"""

import logging
from typing import Dict, Any, Optional

from django.conf import settings
from django.utils import timezone

from ...models import (
    PersistentAgent,
    BrowserUseAgentTask,
    BrowserUseAgentTaskStep,
    PersistentAgentSecret,
)
from ..core.budget import get_current_context as get_budget_context, AgentBudgetManager

logger = logging.getLogger(__name__)


def _max_active_tasks() -> Optional[int]:
    """Return the configured max active browser task limit or None when unlimited."""
    try:
        value = int(getattr(settings, "BROWSER_AGENT_MAX_ACTIVE_TASKS", 3))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _daily_task_limit() -> Optional[int]:
    """Return the configured daily browser task limit or None when unlimited."""
    try:
        value = int(getattr(settings, "BROWSER_AGENT_DAILY_MAX_TASKS", 60))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def get_spawn_web_task_tool() -> Dict[str, Any]:
    """Return the spawn_web_task tool definition for the LLM."""
    max_tasks = _max_active_tasks()
    daily_limit = _daily_task_limit()
    limit_bits = []
    if max_tasks:
        limit_bits.append(f"Maximum {max_tasks} active tasks at once.")
    if daily_limit:
        limit_bits.append(f"Maximum {daily_limit} browser tasks per day.")
    if not limit_bits:
        limit_bits.append("Task limits enforced per deployment settings.")
    limit_sentence = " ".join(limit_bits)

    return {
        "type": "function",
        "function": {
            "name": "spawn_web_task",
            "description": (
                "Spawn a new web automation task that runs asynchronously. Returns immediately with task_id. Be very detailed and specific in your instructions. "
                "Give instructions an AI web browsing agent could realistically complete. If you need URLs, you will need to ask for them. "
                "If you mention secrets, mention them using their direct name, e.g. google_username, not <<<google_username>>>. "
                "Use stored secrets for classic username/password logins only. Do NOT request or attempt to use OAuth credentials (Google, Slack, etc.); "
                "those are handled via MCP tools using connect/auth links. File uploads and downloads are NOT currently supported!!! "
                f"You will be automatically notified when the task completes and can see results in your context. {limit_sentence}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Task prompt."},
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
    from ...encryption import SecretsEncryption

    prompt = params.get("prompt")
    if not prompt:
        return {"status": "error", "message": "Missing required parameter: prompt"}

    # Get optional secrets parameter
    requested_secrets = params.get("secrets", [])
    
    browser_use_agent = agent.browser_use_agent

    # Check active task limit from settings (per agent)
    active_count = BrowserUseAgentTask.objects.filter(
        agent=browser_use_agent,
        status__in=[
            BrowserUseAgentTask.StatusChoices.PENDING,
            BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
        ]
    ).count()

    max_tasks = _max_active_tasks()
    if max_tasks and active_count >= max_tasks:
        return {
            "status": "error", 
            "message": f"Maximum active task limit reached ({max_tasks}). Currently have {active_count} active tasks."
        }

    # Check daily task creation limit
    daily_limit = _daily_task_limit()
    if daily_limit:
        start_of_day = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = BrowserUseAgentTask.objects.filter(
            agent=browser_use_agent,
            created_at__gte=start_of_day,
        ).count()
        if daily_count >= daily_limit:
            return {
                "status": "error",
                "message": (
                    f"Daily browser task limit reached ({daily_limit}). "
                    f"You have already started {daily_count} task(s) today."
                ),
            }
    
    # Log web task creation
    prompt_preview = prompt[:200] + "..." if len(prompt) > 200 else prompt
    logger.info(
        "Agent %s spawning web task: %s%s",
        agent.id, prompt_preview,
        f" (with secrets: {', '.join(requested_secrets)})" if requested_secrets else ""
    )

    try:
        # ---------------- Recursion gating ---------------- #
        budget_ctx = get_budget_context()
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

        task = BrowserUseAgentTask.objects.create(
            agent=browser_use_agent,
            user=agent.user,
            prompt=prompt,
        )

        # Copy secrets from persistent agent to browser task (exclude requested secrets)
        agent_secrets = agent.secrets.filter(requested=False)
        
        # Filter secrets if specific ones were requested
        if requested_secrets:
            # Validate that all requested secret keys exist
            available_secret_keys = set(agent_secrets.values_list('key', flat=True))
            missing_secrets = set(requested_secrets) - available_secret_keys
            
            if missing_secrets:
                return {
                    "status": "error", 
                    "message": f"Requested secret keys not found: {', '.join(sorted(missing_secrets))}. Available secret keys: {', '.join(sorted(available_secret_keys)) if available_secret_keys else 'none'}"
                }
            
            # Filter to only requested secrets
            agent_secrets = agent_secrets.filter(key__in=requested_secrets)
        
        if agent_secrets.exists():
            # Convert table-based secrets to JSON format for the task
            secrets_by_domain = {}
            secret_keys_by_domain = {}
            
            for secret in agent_secrets:
                domain = secret.domain_pattern
                if domain not in secrets_by_domain:
                    secrets_by_domain[domain] = {}
                    secret_keys_by_domain[domain] = []
                
                # Decrypt the secret value for the task
                secrets_by_domain[domain][secret.key] = secret.get_value()
                secret_keys_by_domain[domain].append(secret.key)
            
            # Encrypt the consolidated secrets for the task
            task.encrypted_secrets = SecretsEncryption.encrypt_secrets(secrets_by_domain, allow_legacy=False)
            task.secret_keys = secret_keys_by_domain
            task.save(update_fields=['encrypted_secrets', 'secret_keys'])

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
