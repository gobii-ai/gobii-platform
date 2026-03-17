import json
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from uuid import UUID

from django.conf import settings
from django.db.models import CharField, Count, DateTimeField, IntegerField, OuterRef, Q, Subquery
from django.utils import timezone

from api.agent.core.processing_flags import pending_set_key
from api.models import (
    AgentComputeSession,
    BrowserUseAgentTask,
    PersistentAgent,
    ProxyHealthCheckResult,
    ProxyServer,
)
from api.services.web_sessions import (
    WEB_SESSION_TTL_SECONDS,
    get_live_web_sessions_for_environment,
)
from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

SYSTEM_STATUS_POLL_INTERVAL_SECONDS = 30
SYSTEM_STATUS_PROXY_FRESHNESS_HOURS = 24
SYSTEM_STATUS_ROW_LIMIT = 20

STATUS_HEALTHY = "healthy"
STATUS_WARNING = "warning"
STATUS_CRITICAL = "critical"
STATUS_INFO = "info"


def build_system_status_payload():
    now = timezone.now()
    sections = {}

    section_collectors = {
        "celery": _collect_celery_section,
        "agents": _collect_agent_processing_section,
        "webSessions": _collect_web_session_section,
        "compute": _collect_compute_section,
        "proxies": _collect_proxy_section,
        "browserTasks": _collect_browser_task_section,
    }

    for section_name, collector in section_collectors.items():
        sections[section_name] = _safe_collect_section(collector, now=now)

    return {
        "meta": {
            "environment": getattr(settings, "GOBII_RELEASE_ENV", "local"),
            "refreshedAt": now.isoformat(),
            "pollIntervalSeconds": SYSTEM_STATUS_POLL_INTERVAL_SECONDS,
        },
        "overview": _build_overview_cards(sections),
        "sections": sections,
    }


def _safe_collect_section(collector, *, now):
    try:
        return collector(now=now)
    except Exception:
        logger.exception("System status collector failed: %s", getattr(collector, "__name__", "unknown"))
        return {
            "available": False,
            "status": STATUS_CRITICAL,
            "summary": {},
            "rows": [],
            "error": "Temporarily unavailable.",
        }


def _collect_celery_section(*, now):
    redis_client = get_redis_client()
    queue_names = ("celery", "celery.single_instance")
    rows = []
    total_pending = 0

    for queue_name in queue_names:
        pending_count = _safe_redis_int(redis_client.llen(queue_name))
        total_pending += pending_count
        rows.append(
            {
                "queue": queue_name,
                "pendingCount": pending_count,
            }
        )

    return {
        "available": True,
        "status": _celery_status(total_pending),
        "summary": {
            "totalPending": total_pending,
            "queueCounts": {row["queue"]: row["pendingCount"] for row in rows},
        },
        "rows": rows,
    }


def _collect_agent_processing_section(*, now):
    redis_client = get_redis_client()
    agent_state_map = {}

    for key in _iter_redis_keys(redis_client, "agent-event-processing:heartbeat:*"):
        agent_id = _uuid_from_suffix(key)
        if not agent_id:
            continue
        state = agent_state_map.setdefault(agent_id, _empty_agent_state(agent_id))
        state["heartbeat"] = True
        heartbeat_payload = _load_redis_json(redis_client.get(key))
        stage = str(heartbeat_payload.get("stage") or "").strip()
        if stage:
            state["stage"] = stage
        last_seen = _coerce_timestamp(heartbeat_payload.get("last_seen"))
        if last_seen is not None:
            state["lastSeenAt"] = last_seen.isoformat()

    for key in _iter_redis_keys(redis_client, "agent-event-processing:queued:*"):
        agent_id = _uuid_from_suffix(key)
        if not agent_id:
            continue
        state = agent_state_map.setdefault(agent_id, _empty_agent_state(agent_id))
        state["queued"] = True

    pending_ids = getattr(redis_client, "smembers", lambda _key: set())(pending_set_key())
    for raw_agent_id in pending_ids:
        agent_id = _normalize_uuid(raw_agent_id)
        if not agent_id:
            continue
        state = agent_state_map.setdefault(agent_id, _empty_agent_state(agent_id))
        state["pending"] = True

    for key in _iter_redis_keys(redis_client, "redlock:agent-event-processing:*"):
        agent_id = _uuid_from_suffix(key)
        if not agent_id:
            continue
        state = agent_state_map.setdefault(agent_id, _empty_agent_state(agent_id))
        state["locked"] = True

    for key in _iter_redis_keys(redis_client, "agent-event-processing:*"):
        suffix = key.replace("agent-event-processing:", "", 1)
        agent_id = _normalize_uuid(suffix)
        if not agent_id:
            continue
        state = agent_state_map.setdefault(agent_id, _empty_agent_state(agent_id))
        state["locked"] = True

    if not agent_state_map:
        return {
            "available": True,
            "status": STATUS_HEALTHY,
            "summary": {
                "activeAgentCount": 0,
                "queuedCount": 0,
                "pendingCount": 0,
                "lockedCount": 0,
                "heartbeatCount": 0,
                "queuedOrPendingCount": 0,
            },
            "rows": [],
        }

    current_env = getattr(settings, "GOBII_RELEASE_ENV", "local")
    agent_ids = [UUID(agent_id) for agent_id in agent_state_map.keys()]
    agents = {
        str(agent.id): agent
        for agent in PersistentAgent.objects.filter(
            id__in=agent_ids,
            execution_environment=current_env,
            is_deleted=False,
        ).only("id", "name")
    }

    rows = []
    for agent_id, state in agent_state_map.items():
        agent = agents.get(agent_id)
        if agent is None:
            continue
        row = dict(state)
        row["agentName"] = agent.name
        rows.append(row)

    filtered_states = [agent_state_map[agent_id] for agent_id in agents.keys()]

    rows.sort(
        key=lambda row: (
            0 if row["pending"] or row["queued"] else 1,
            0 if row["locked"] else 1,
            0 if row["heartbeat"] else 1,
            row.get("agentName", "").lower(),
        )
    )
    rows = rows[:SYSTEM_STATUS_ROW_LIMIT]

    summary = {
        "activeAgentCount": len(agents),
        "queuedCount": sum(1 for state in filtered_states if state["queued"]),
        "pendingCount": sum(1 for state in filtered_states if state["pending"]),
        "lockedCount": sum(1 for state in filtered_states if state["locked"]),
        "heartbeatCount": sum(1 for state in filtered_states if state["heartbeat"]),
        "queuedOrPendingCount": sum(
            1 for state in filtered_states if state["queued"] or state["pending"]
        ),
    }

    if summary["queuedOrPendingCount"] >= 20:
        status = STATUS_CRITICAL
    elif summary["queuedOrPendingCount"] > 0:
        status = STATUS_WARNING
    elif summary["activeAgentCount"] > 0:
        status = STATUS_INFO
    else:
        status = STATUS_HEALTHY

    return {
        "available": True,
        "status": status,
        "summary": summary,
        "rows": rows,
    }


def _collect_web_session_section(*, now):
    ttl_seconds = WEB_SESSION_TTL_SECONDS
    current_env = getattr(settings, "GOBII_RELEASE_ENV", "local")
    sessions = list(
        get_live_web_sessions_for_environment(
            current_env,
            ttl_seconds=ttl_seconds,
            now=now,
        )
    )
    live_count = len(sessions)
    rows = [
        {
            "sessionId": str(session.id),
            "agentId": str(session.agent_id),
            "agentName": session.agent.name,
            "userEmail": session.user.email,
            "startedAt": session.started_at.isoformat(),
            "lastSeenAt": session.last_seen_at.isoformat(),
            "lastSeenSource": session.last_seen_source,
        }
        for session in sessions[:SYSTEM_STATUS_ROW_LIMIT]
    ]

    return {
        "available": True,
        "status": STATUS_INFO if live_count else STATUS_HEALTHY,
        "summary": {
            "liveCount": live_count,
            "ttlSeconds": ttl_seconds,
        },
        "rows": rows,
    }


def _collect_compute_section(*, now):
    current_env = getattr(settings, "GOBII_RELEASE_ENV", "local")
    sessions = list(
        AgentComputeSession.objects.filter(
            agent__execution_environment=current_env,
            agent__is_deleted=False,
        )
        .select_related("agent", "proxy_server")
    )

    summary = {
        "runningCount": 0,
        "idleStoppingCount": 0,
        "stoppedCount": 0,
        "errorCount": 0,
    }
    state_key_map = {
        AgentComputeSession.State.RUNNING: "runningCount",
        AgentComputeSession.State.IDLE_STOPPING: "idleStoppingCount",
        AgentComputeSession.State.STOPPED: "stoppedCount",
        AgentComputeSession.State.ERROR: "errorCount",
    }

    rows = []
    for session in sessions:
        summary[state_key_map[session.state]] += 1
        if session.state not in {
            AgentComputeSession.State.RUNNING,
            AgentComputeSession.State.IDLE_STOPPING,
            AgentComputeSession.State.ERROR,
        }:
            continue
        rows.append(
            {
                "agentId": str(session.agent_id),
                "agentName": session.agent.name,
                "state": session.state,
                "namespace": session.namespace,
                "podName": session.pod_name,
                "proxyName": session.proxy_server.name if session.proxy_server else "",
                "lastActivityAt": _iso_or_empty(session.last_activity_at),
                "leaseExpiresAt": _iso_or_empty(session.lease_expires_at),
            }
        )

    priority = {
        AgentComputeSession.State.ERROR: 0,
        AgentComputeSession.State.IDLE_STOPPING: 1,
        AgentComputeSession.State.RUNNING: 2,
    }
    rows.sort(key=lambda row: (priority.get(row["state"], 99), row["agentName"].lower()))

    if summary["errorCount"] > 0:
        status = STATUS_CRITICAL
    elif summary["idleStoppingCount"] > 0:
        status = STATUS_WARNING
    else:
        status = STATUS_HEALTHY

    return {
        "available": True,
        "status": status,
        "summary": summary,
        "rows": rows[:SYSTEM_STATUS_ROW_LIMIT],
    }


def _collect_proxy_section(*, now):
    freshness_cutoff = now - timedelta(hours=SYSTEM_STATUS_PROXY_FRESHNESS_HOURS)
    latest_results = ProxyHealthCheckResult.objects.filter(proxy_server=OuterRef("pk")).order_by("-checked_at")
    proxies = list(
        ProxyServer.objects.annotate(
            latest_status=Subquery(latest_results.values("status")[:1], output_field=CharField()),
            latest_checked_at=Subquery(latest_results.values("checked_at")[:1], output_field=DateTimeField()),
            latest_response_time_ms=Subquery(
                latest_results.values("response_time_ms")[:1],
                output_field=IntegerField(),
            ),
        ).order_by("name", "host", "port")
    )

    summary = {
        "activeCount": 0,
        "healthyCount": 0,
        "degradedCount": 0,
        "staleCount": 0,
        "inactiveCount": 0,
    }
    rows = []
    for proxy in proxies:
        classification = _classify_proxy(proxy, freshness_cutoff)
        summary[_proxy_summary_key(classification)] += 1
        if classification != "inactive":
            summary["activeCount"] += 1
        rows.append(
            {
                "proxyId": str(proxy.id),
                "name": proxy.name,
                "endpoint": f"{proxy.host}:{proxy.port}",
                "classification": classification,
                "isActive": proxy.is_active,
                "latestStatus": proxy.latest_status or "",
                "latestCheckedAt": _iso_or_empty(proxy.latest_checked_at),
                "responseTimeMs": proxy.latest_response_time_ms,
                "consecutiveHealthFailures": proxy.consecutive_health_failures,
                "deactivationReason": proxy.deactivation_reason,
            }
        )

    rows.sort(
        key=lambda row: (
            {"degraded": 0, "stale": 1, "healthy": 2, "inactive": 3}.get(row["classification"], 99),
            row["name"].lower(),
            row["endpoint"],
        )
    )

    if summary["activeCount"] == 0:
        status = STATUS_INFO
    elif summary["degradedCount"] > 0:
        status = STATUS_CRITICAL
    elif summary["staleCount"] > 0:
        status = STATUS_WARNING
    else:
        status = STATUS_HEALTHY

    return {
        "available": True,
        "status": status,
        "summary": summary,
        "rows": rows[:SYSTEM_STATUS_ROW_LIMIT],
    }


def _collect_browser_task_section(*, now):
    current_env = getattr(settings, "GOBII_RELEASE_ENV", "local")
    task_queryset = (
        BrowserUseAgentTask.objects.alive()
        .filter(
            Q(agent__persistent_agent__execution_environment=current_env)
            | Q(agent__persistent_agent__isnull=True)
            | Q(agent__isnull=True)
        )
        .filter(
            status__in=[
                BrowserUseAgentTask.StatusChoices.PENDING,
                BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
                BrowserUseAgentTask.StatusChoices.FAILED,
            ]
        )
        .select_related("agent")
    )

    counts = {
        row["status"]: row["count"]
        for row in task_queryset.values("status").annotate(count=Count("status"))
    }
    pending_count = counts.get(BrowserUseAgentTask.StatusChoices.PENDING, 0)
    in_progress_count = counts.get(BrowserUseAgentTask.StatusChoices.IN_PROGRESS, 0)
    failed_count = counts.get(BrowserUseAgentTask.StatusChoices.FAILED, 0)
    active_count = pending_count + in_progress_count

    rows = [
        {
            "taskId": str(task.id),
            "agentName": task.agent.name if task.agent_id else "",
            "status": task.status,
            "createdAt": task.created_at.isoformat(),
            "updatedAt": task.updated_at.isoformat(),
            "errorMessage": (task.error_message or "")[:160],
        }
        for task in task_queryset.order_by("-updated_at")[:SYSTEM_STATUS_ROW_LIMIT]
    ]

    if active_count >= 50:
        status = STATUS_CRITICAL
    elif active_count > 0 or failed_count > 0:
        status = STATUS_WARNING
    else:
        status = STATUS_HEALTHY

    return {
        "available": True,
        "status": status,
        "summary": {
            "pendingCount": pending_count,
            "inProgressCount": in_progress_count,
            "failedCount": failed_count,
            "activeCount": active_count,
        },
        "rows": rows,
    }


def _build_overview_cards(sections):
    return [
        _overview_card(
            card_id="celery-backlog",
            label="Celery Pending",
            section=sections.get("celery"),
            value_key="totalPending",
            subtitle_builder=lambda summary: "Across celery and single-instance queues",
        ),
        _overview_card(
            card_id="processing-agents",
            label="Agents Processing",
            section=sections.get("agents"),
            value_key="activeAgentCount",
            subtitle_builder=lambda summary: (
                f"{summary.get('queuedOrPendingCount', 0)} queued or pending"
                if summary
                else ""
            ),
        ),
        _overview_card(
            card_id="web-sessions",
            label="Live Web Sessions",
            section=sections.get("webSessions"),
            value_key="liveCount",
            subtitle_builder=lambda summary: f"TTL {summary.get('ttlSeconds', 0)}s",
        ),
        _overview_card(
            card_id="compute-running",
            label="Compute Running",
            section=sections.get("compute"),
            value_key="runningCount",
            subtitle_builder=lambda summary: (
                f"{summary.get('errorCount', 0)} errors"
                if summary and summary.get("errorCount", 0)
                else "Per-agent sandbox sessions"
            ),
        ),
        _overview_card(
            card_id="proxy-health",
            label="Healthy Proxies",
            section=sections.get("proxies"),
            value_builder=lambda summary: f"{summary.get('healthyCount', 0)} / {summary.get('activeCount', 0)}",
            subtitle_builder=lambda summary: (
                f"{summary.get('staleCount', 0)} stale, {summary.get('degradedCount', 0)} degraded"
            ),
        ),
        _overview_card(
            card_id="browser-task-backlog",
            label="Browser Task Backlog",
            section=sections.get("browserTasks"),
            value_key="activeCount",
            subtitle_builder=lambda summary: f"{summary.get('failedCount', 0)} failed",
        ),
    ]


def _overview_card(*, card_id, label, section, value_key=None, value_builder=None, subtitle_builder=None):
    if not section or not section.get("available"):
        return {
            "id": card_id,
            "label": label,
            "value": "Unavailable",
            "status": STATUS_CRITICAL,
            "subtitle": section.get("error") if section else "Temporarily unavailable.",
        }

    summary = section.get("summary") or {}
    value = value_builder(summary) if value_builder else summary.get(value_key)
    subtitle = subtitle_builder(summary) if subtitle_builder else ""
    return {
        "id": card_id,
        "label": label,
        "value": value if value not in (None, "") else 0,
        "status": section.get("status", STATUS_INFO),
        "subtitle": subtitle,
    }


def _iter_redis_keys(redis_client, pattern):
    scan_iter = getattr(redis_client, "scan_iter", None)
    if callable(scan_iter):
        keys = scan_iter(match=pattern)
    else:
        keys_method = getattr(redis_client, "keys", None)
        if not callable(keys_method):
            return []
        keys = keys_method(pattern)

    return [_decode_redis_value(key) for key in keys]


def _decode_redis_value(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "ignore")
    return str(value)


def _load_redis_json(raw_value):
    decoded = _decode_redis_value(raw_value) if raw_value is not None else ""
    if not decoded:
        return {}
    try:
        payload = json.loads(decoded)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_uuid(raw_value):
    try:
        return str(UUID(_decode_redis_value(raw_value)))
    except (TypeError, ValueError, AttributeError):
        return ""


def _uuid_from_suffix(key):
    suffix = key.rsplit(":", 1)[-1]
    return _normalize_uuid(suffix)


def _coerce_timestamp(raw_value):
    if raw_value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(raw_value), tz=dt_timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _empty_agent_state(agent_id):
    return {
        "agentId": agent_id,
        "heartbeat": False,
        "queued": False,
        "pending": False,
        "locked": False,
        "stage": "",
        "lastSeenAt": "",
    }


def _classify_proxy(proxy, freshness_cutoff):
    if not proxy.is_active:
        return "inactive"
    if not proxy.latest_checked_at or proxy.latest_checked_at < freshness_cutoff:
        return "stale"
    if proxy.latest_status == ProxyHealthCheckResult.Status.PASSED:
        return "healthy"
    return "degraded"


def _proxy_summary_key(classification):
    return {
        "healthy": "healthyCount",
        "degraded": "degradedCount",
        "stale": "staleCount",
        "inactive": "inactiveCount",
    }[classification]


def _celery_status(total_pending):
    if total_pending >= 50:
        return STATUS_CRITICAL
    if total_pending >= 10:
        return STATUS_WARNING
    return STATUS_HEALTHY


def _safe_redis_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso_or_empty(value):
    return value.isoformat() if value else ""
