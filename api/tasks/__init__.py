# --------------------------------------------------------------------------- #
#  Backward compatibility shim for tasks.py refactoring
#  
#  This file imports all tasks from their new domain-specific modules to
#  maintain backward compatibility with existing imports and Celery beat schedules.
# --------------------------------------------------------------------------- #

# Import all tasks from their new modules
from .browser_agent_tasks import (
    process_browser_use_task,
    _process_browser_use_task_core,
    select_proxy_for_task,
    _run_agent,
    _safe_aclose,
    _jsonify,
)

from .proxy_tasks import (
    sync_all_ip_blocks,
    sync_ip_block,
    backfill_missing_proxy_records,
    proxy_health_check_nightly,
    proxy_health_check_single,
    decodo_low_inventory_reminder,
    _perform_proxy_health_check,
    _fetch_decodo_ip_data,
    _update_or_create_ip_record,
    _update_or_create_proxy_record,
)

from .subscription_tasks import (
    grant_monthly_free_credits,
)

from .maintenance_tasks import (
    cleanup_temp_files,
    garbage_collect_timed_out_tasks,
    prune_prompt_archives,
)

# Sandbox compute tasks
from .sandbox_compute import discover_mcp_tools  # noqa: F401
from .sandbox_compute_lifecycle import sweep_idle_sandbox_sessions  # noqa: F401

# Soft-expiration task (global sweeper)
from .soft_expiration_task import soft_expire_inactive_agents_task

# Billing rollup / Stripe metering
from .billing_rollup import rollup_and_meter_usage_task

# Burn rate snapshot refresh
from .burn_rate_snapshots import refresh_burn_rate_snapshots_task  # noqa: F401

# Proactive agent scheduler
from .proactive_agents import schedule_proactive_agents_task  # noqa: F401

# Avatar backfill scheduler
from .avatar_backfill import schedule_agent_avatar_backfill_task  # noqa: F401

# Ensure persistent-agent task modules (IMAP polling, event processing) are imported
# so Celery autodiscovery picks them up when it imports api.tasks.
# Without this, tasks under `api.agent.tasks.*` may not register on the worker
# unless some other code imports them first (e.g., console views).
import api.agent.tasks  # noqa: F401

# Ensure eval tasks are registered
import api.evals.tasks  # noqa: F401

# Agent lifecycle cleanup task (one-stop shutdown cleanup)
from .agent_lifecycle import agent_shutdown_cleanup_task  # noqa: F401
