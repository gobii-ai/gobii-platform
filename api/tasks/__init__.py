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
)

# Soft-expiration task (global sweeper)
from .soft_expiration_task import soft_expire_inactive_agents_task

# Billing rollup / Stripe metering
from .billing_rollup import rollup_and_meter_usage_task

# Ensure persistent-agent task modules (IMAP polling, event processing) are imported
# so Celery autodiscovery picks them up when it imports api.tasks.
# Without this, tasks under `api.agent.tasks.*` may not register on the worker
# unless some other code imports them first (e.g., console views).
import api.agent.tasks  # noqa: F401
