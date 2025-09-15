import logging
from datetime import timedelta
from typing import Dict, List, Set

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from api.integrations.pipedream_connect_gc import (
    iter_accounts,
    delete_external_user,
    delete_account,
    extract_external_user_id,
    extract_account_id,
)
from api.models import PersistentAgent


logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.pipedream_connect_gc.gc_orphaned_users", bind=True, ignore_result=True)
def gc_orphaned_users(self) -> Dict[str, int]:
    """
    Nightly batch GC for Pipedream Connect resources.

    Deletes external users (and thus their accounts) when:
    - No matching PersistentAgent exists for the external_user_id (true orphan), or
    - Agent is EXPIRED and beyond retention window, or
    - Agent is deactivated and beyond retention window.

    Returns summary counters for observability.
    """
    # Feature guard
    if not getattr(settings, "PIPEDREAM_GC_ENABLED", False):
        logger.info("Pipedream GC: disabled by settings; exiting")
        return {"scanned_accounts": 0, "pd_users": 0, "candidates": 0, "deleted_users": 0, "deleted_accounts": 0}

    dry_run = bool(getattr(settings, "PIPEDREAM_GC_DRY_RUN", True))
    page_size = int(getattr(settings, "PIPEDREAM_GC_BATCH_SIZE", 200))
    max_deletes = int(getattr(settings, "PIPEDREAM_GC_MAX_DELETES_PER_RUN", 200))

    now = timezone.now()
    expired_retention_days = int(getattr(settings, "PIPEDREAM_GC_EXPIRED_RETENTION_DAYS", 30))
    deactivated_retention_days = int(getattr(settings, "PIPEDREAM_GC_DEACTIVATED_RETENTION_DAYS", 60))
    expired_cutoff = now - timedelta(days=expired_retention_days)
    deactivated_cutoff = now - timedelta(days=deactivated_retention_days)

    # Build PD maps: user -> [account_ids]
    scanned_accounts = 0
    pd_user_to_accounts: Dict[str, List[str]] = {}
    unknown_owner_accounts: int = 0
    for acct in iter_accounts(page_size=page_size):
        scanned_accounts += 1
        uid = extract_external_user_id(acct)
        acct_id = extract_account_id(acct)
        if not uid:
            unknown_owner_accounts += 1
            continue
        if not acct_id:
            continue
        pd_user_to_accounts.setdefault(uid, []).append(acct_id)

    pd_user_ids: Set[str] = set(pd_user_to_accounts.keys())

    # Load matching agents for any PD users we saw
    existing_agents: Dict[str, PersistentAgent] = {}
    if pd_user_ids:
        for agent in PersistentAgent.objects.filter(id__in=list(pd_user_ids)).only(
            "id", "life_state", "is_active", "last_interaction_at", "last_expired_at"
        ):
            existing_agents[str(agent.id)] = agent

    # Decide candidates
    candidates: List[str] = []
    keep: int = 0
    for uid in pd_user_ids:
        ag = existing_agents.get(uid)
        if ag is None:
            candidates.append(uid)
            continue
        if getattr(ag, "life_state", None) == PersistentAgent.LifeState.EXPIRED:
            if ag.last_expired_at and ag.last_expired_at <= expired_cutoff:
                candidates.append(uid)
                continue
        if ag.is_active is False:
            last_int = ag.last_interaction_at
            if last_int and last_int <= deactivated_cutoff:
                candidates.append(uid)
                continue
        keep += 1

    # Enforce per-run max deletions
    if len(candidates) > max_deletes:
        logger.info(
            "Pipedream GC: truncating candidates from %d to max %d",
            len(candidates), max_deletes,
        )
        candidates = candidates[:max_deletes]

    deleted_users = 0
    deleted_accounts = 0

    for uid in candidates:
        if dry_run:
            logger.info("Pipedream GC (dry-run): would delete external_user_id=%s accounts=%d",
                        uid, len(pd_user_to_accounts.get(uid, [])))
            continue

        ok, status, msg = delete_external_user(uid)
        if ok:
            deleted_users += 1
            continue

        # Fallback: delete accounts individually
        logger.warning("Pipedream GC: user delete failed for uid=%s status=%s; falling back to per-account: %s", uid, status, msg)
        for acc_id in pd_user_to_accounts.get(uid, []):
            ok_acc, st_acc, msg_acc = delete_account(acc_id)
            if ok_acc:
                deleted_accounts += 1
            else:
                logger.error("Pipedream GC: account delete failed uid=%s account=%s status=%s msg=%s", uid, acc_id, st_acc, msg_acc)

    result = {
        "scanned_accounts": scanned_accounts,
        "pd_users": len(pd_user_ids),
        "candidates": len(candidates),
        "deleted_users": deleted_users,
        "deleted_accounts": deleted_accounts,
        "unknown_owner_accounts": unknown_owner_accounts,
        "kept_users": keep,
        "dry_run": int(dry_run),
    }
    logger.info("Pipedream GC summary: %s", result)
    return result

