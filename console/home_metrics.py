import logging
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, Sum
from django.utils import timezone

from agents.services import AgentService
from api.models import BrowserUseAgentTask, Organization, OrganizationMembership, PersistentAgent

logger = logging.getLogger(__name__)

CONSOLE_HOME_CACHE_VERSION = 1
CONSOLE_HOME_CACHE_FRESH_SECONDS = 45
CONSOLE_HOME_CACHE_STALE_SECONDS = 600
CONSOLE_HOME_CACHE_LOCK_SECONDS = 60


def _console_home_cache_key(owner_type: str, owner_id: object) -> str:
    return f"console:home_metrics:v{CONSOLE_HOME_CACHE_VERSION}:{owner_type}:{owner_id}"


def _console_home_cache_lock_key(owner_type: str, owner_id: object) -> str:
    return f"{_console_home_cache_key(owner_type, owner_id)}:refresh_lock"


def _resolve_console_home_owner(request, current_context: dict, current_membership):
    ctx_type = current_context.get("type", "personal")
    org_id = current_context.get("id")
    if ctx_type == "organization" and org_id:
        membership = current_membership
        organization = None
        if membership and str(membership.org_id) == str(org_id):
            organization = getattr(membership, "org", None)
        if organization is None:
            organization = Organization.objects.filter(pk=org_id).first()
        if (
            organization is not None
            and OrganizationMembership.objects.filter(
                user=request.user,
                org_id=organization.id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).exists()
        ):
            return "organization", organization, True
    return "user", request.user, False


def _build_task_status_counts(task_stats) -> dict[str, int]:
    completed_count = in_progress_count = pending_count = failed_count = cancelled_count = 0
    for stat in task_stats:
        status = stat["status"]
        count = stat["count"]
        if status == "completed":
            completed_count = count
        elif status == "in_progress":
            in_progress_count = count
        elif status == "pending":
            pending_count = count
        elif status == "failed":
            failed_count = count
        elif status == "cancelled":
            cancelled_count = count

    return {
        "completed_tasks": completed_count,
        "in_progress_tasks": in_progress_count,
        "pending_tasks": pending_count,
        "failed_tasks": failed_count,
        "cancelled_tasks": cancelled_count,
        "total_active_tasks": in_progress_count + pending_count,
    }


def _build_console_home_metrics_for_owner(owner, *, is_org: bool) -> dict[str, object]:
    agent_count = AgentService.get_agents_in_use(owner)
    if is_org:
        pa_browser_ids = (
            PersistentAgent.objects.non_eval()
            .filter(organization_id=owner.id)
            .values_list("browser_use_agent_id", flat=True)
        )
        task_stats = (
            BrowserUseAgentTask.objects.filter(agent_id__in=pa_browser_ids, is_deleted=False)
            .values("status")
            .annotate(count=Count("status"))
        )
    else:
        task_stats = (
            BrowserUseAgentTask.objects.filter(user=owner, is_deleted=False)
            .values("status")
            .annotate(count=Count("status"))
        )

    metrics = {"agent_count": agent_count}
    metrics.update(_build_task_status_counts(task_stats))

    if is_org:
        TaskCredit = apps.get_model("api", "TaskCredit")
        now = timezone.now()
        qs = TaskCredit.objects.filter(
            organization_id=owner.id,
            granted_date__lte=now,
            expiration_date__gte=now,
            voided=False,
        )
        agg = qs.aggregate(
            avail=Sum("available_credits"),
            total=Sum("credits"),
            used=Sum("credits_used"),
        )

        def _to_decimal(value):
            if value is None:
                return Decimal("0")
            return value if isinstance(value, Decimal) else Decimal(value)

        org_tasks_available = agg["avail"] if agg["avail"] is not None else Decimal("0")
        total = _to_decimal(agg["total"])
        used = _to_decimal(agg["used"])

        if total == 0:
            tasks_used_pct = Decimal("0")
        else:
            usage_pct = (used / total) * Decimal("100")
            tasks_used_pct = min(usage_pct, Decimal("100"))

        metrics["org_tasks_available"] = org_tasks_available
        metrics["org_tasks_used_pct"] = float(tasks_used_pct)

    return metrics


def _enqueue_console_home_refresh(owner_type: str, owner_id: object) -> None:
    lock_key = _console_home_cache_lock_key(owner_type, owner_id)
    if not cache.add(lock_key, "1", timeout=CONSOLE_HOME_CACHE_LOCK_SECONDS):
        return

    try:
        from console.tasks import refresh_console_home_cache

        refresh_console_home_cache.delay(owner_type, str(owner_id))
    except Exception:
        cache.delete(lock_key)
        logger.exception("Failed to enqueue console home refresh for %s %s", owner_type, owner_id)


def get_console_home_metrics(request, current_context: dict, current_membership) -> dict[str, object]:
    owner_type, owner, is_org = _resolve_console_home_owner(
        request,
        current_context,
        current_membership,
    )
    cache_key = _console_home_cache_key(owner_type, owner.id)
    cached = cache.get(cache_key)
    now_ts = timezone.now().timestamp()

    if isinstance(cached, dict):
        cached_data = cached.get("data")
        refreshed_at = cached.get("refreshed_at")
        if cached_data is not None and refreshed_at is not None:
            age_seconds = max(0, now_ts - refreshed_at)
            if age_seconds <= CONSOLE_HOME_CACHE_FRESH_SECONDS:
                return cached_data
            if age_seconds <= CONSOLE_HOME_CACHE_STALE_SECONDS:
                _enqueue_console_home_refresh(owner_type, owner.id)
                return cached_data

    metrics = _build_console_home_metrics_for_owner(owner, is_org=is_org)
    cache.set(
        cache_key,
        {"data": metrics, "refreshed_at": now_ts},
        timeout=CONSOLE_HOME_CACHE_STALE_SECONDS,
    )
    return metrics


def load_console_home_owner(owner_type: str, owner_id: str):
    if owner_type == "organization":
        return Organization.objects.filter(pk=owner_id).first(), True
    if owner_type != "user":
        logger.warning("Console home refresh skipped; unknown owner type: %s", owner_type)
        return None, False

    User = get_user_model()
    try:
        return User.objects.get(pk=owner_id), False
    except User.DoesNotExist:
        return None, False
