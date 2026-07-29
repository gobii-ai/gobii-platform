"""Celery polling and reconciliation for durable outbound MCP tasks."""

import logging
import random
import uuid
from datetime import timedelta
from typing import Optional

import httpx
from celery import shared_task
from kombu.exceptions import OperationalError as KombuOperationalError
from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone
from opentelemetry import metrics

from api.agent.tools.mcp_manager import get_mcp_manager
from api.agent.tools.mcp_task_protocol import MCPTaskHTTPError, MCPTaskProtocolError
from api.models import PersistentAgentMCPTask
from api.services.agent_background_follow_up import enqueue_agent_background_follow_up
from api.services.owner_execution_pause import is_owner_execution_paused, resolve_agent_owner


logger = logging.getLogger(__name__)
REMOTE_CANCEL_ERRORS = (
    httpx.HTTPError,
    MCPTaskProtocolError,
    ValueError,
    RuntimeError,
    DatabaseError,
)
_lifecycle_counter = metrics.get_meter("gobii.mcp_tasks").create_counter(
    "gobii.mcp_tasks.lifecycle",
    description="Outbound MCP task lifecycle transitions",
)


def record_mcp_task_lifecycle(event: str, task: PersistentAgentMCPTask, **attributes) -> None:
    dimensions = {
        "event": event,
        "status": task.status,
        "server_name": task.server_name,
        **attributes,
    }
    _lifecycle_counter.add(1, dimensions)
    logger.info(
        "MCP async task lifecycle",
        extra={
            **dimensions,
            "mcp_task_id": str(task.id),
            "remote_task_id": task.remote_task_id,
            "agent_id": str(task.agent_id),
            "server_config_id": str(task.server_config_id or ""),
        },
    )


def _schedule_follow_up(task: PersistentAgentMCPTask) -> None:
    enqueued = enqueue_agent_background_follow_up(
        task.agent,
        budget_id=task.budget_id,
        branch_id=task.branch_id,
        depth=task.depth,
        eval_run_id=task.eval_run_id,
    )
    if not enqueued:
        logger.info("Skipping MCP task wake while owner execution is paused", extra={"mcp_task_id": str(task.id)})


def _dispatch_wake(task_id: str) -> None:
    task = (
        PersistentAgentMCPTask.objects.select_related("agent", "agent__user", "agent__organization")
        .filter(pk=task_id)
        .first()
    )
    if task is None:
        return
    try:
        _schedule_follow_up(task)
    except (KombuOperationalError, RuntimeError):
        PersistentAgentMCPTask.objects.filter(
            pk=task_id,
            wake_enqueued_at=task.wake_enqueued_at,
        ).update(wake_enqueued_at=None)
        logger.warning("Failed to enqueue MCP task wake; reconciliation will retry", exc_info=True)


def _enqueue_wake_locked(task: PersistentAgentMCPTask, now) -> bool:
    if task.wake_enqueued_at is not None:
        record_mcp_task_lifecycle("duplicate_wake_suppressed", task)
        return False
    task.wake_enqueued_at = now
    transaction.on_commit(lambda: _dispatch_wake(str(task.id)))
    return True


def _terminalize_locked(
    task: PersistentAgentMCPTask,
    *,
    status: str,
    message: str = "",
    result=None,
    error=None,
) -> None:
    now = timezone.now()
    task.status = status
    task.status_message = message
    task.result = result
    task.error = error
    task.terminal_at = now
    task.next_poll_at = None
    task.lease_token = None
    task.lease_expires_at = None
    _enqueue_wake_locked(task, now)
    task.save()


def enqueue_mcp_task_poll(task_id: str, *, countdown: float) -> None:
    try:
        poll_mcp_task.apply_async(args=[task_id], countdown=countdown)
    except KombuOperationalError:
        logger.warning(
            "MCP task poll enqueue failed; reconciliation will recover it",
            extra={"mcp_task_id": task_id},
            exc_info=True,
        )


def _claim_task(task_id: str) -> tuple[Optional[PersistentAgentMCPTask], Optional[uuid.UUID]]:
    now = timezone.now()
    with transaction.atomic():
        task = (
            PersistentAgentMCPTask.objects.select_for_update()
            .select_related("agent", "agent__user", "agent__organization")
            .filter(pk=task_id)
            .first()
        )
        if task is None or task.terminal_at is not None:
            return None, None
        if task.lease_expires_at is not None and task.lease_expires_at > now:
            return None, None
        if task.next_poll_at is None or task.next_poll_at > now:
            return None, None
        token = uuid.uuid4()
        task.lease_token = token
        task.lease_expires_at = now + timedelta(
            seconds=max(settings.MCP_ASYNC_TASK_LEASE_SECONDS, 1)
        )
        task.save(update_fields=["lease_token", "lease_expires_at", "updated_at"])
        return task, token


def _retry_delay_seconds(task: PersistentAgentMCPTask) -> float:
    minimum = max(settings.MCP_ASYNC_TASK_MIN_POLL_INTERVAL_SECONDS, 1)
    maximum = max(settings.MCP_ASYNC_TASK_MAX_POLL_INTERVAL_SECONDS, minimum)
    base = max(minimum, task.poll_interval_ms / 1000)
    exponential = min(maximum, base * (2 ** min(max(task.attempts - 1, 0), 8)))
    return max(minimum, min(maximum, exponential * random.uniform(0.8, 1.2)))


def _schedule_retry(task_id: str, lease_token: uuid.UUID, message: str) -> None:
    now = timezone.now()
    should_expire = False
    with transaction.atomic():
        task = PersistentAgentMCPTask.objects.select_for_update().get(pk=task_id)
        if task.terminal_at is not None or task.lease_token != lease_token:
            return
        task.attempts += 1
        delay = _retry_delay_seconds(task)
        if now + timedelta(seconds=delay) >= task.deadline_at:
            should_expire = True
        else:
            task.status_message = message
            task.next_poll_at = now + timedelta(seconds=delay)
            task.lease_token = None
            task.lease_expires_at = None
            task.save()
            transaction.on_commit(
                lambda: enqueue_mcp_task_poll(task_id, countdown=delay)
            )
            record_mcp_task_lifecycle("retried", task, delay_seconds=delay)
    if should_expire:
        _expire_task(task_id, message="The MCP task exceeded its allowed lifetime.")


def _apply_remote_state(task_id: str, lease_token: uuid.UUID, remote) -> None:
    now = timezone.now()
    with transaction.atomic():
        task = (
            PersistentAgentMCPTask.objects.select_for_update()
            .select_related("agent", "agent__user", "agent__organization")
            .get(pk=task_id)
        )
        if task.terminal_at is not None or task.lease_token != lease_token:
            return

        task.status_message = remote.status_message
        task.remote_created_at = remote.created_at
        task.remote_updated_at = remote.last_updated_at
        task.attempts = 0
        if (
            remote.status in PersistentAgentMCPTask.ACTIVE_STATUSES
            and remote.poll_interval_ms is not None
        ):
            task.poll_interval_ms = get_mcp_manager()._clamp_mcp_task_poll_interval_ms(
                remote.poll_interval_ms
            )
        if (
            remote.status in PersistentAgentMCPTask.ACTIVE_STATUSES
            and remote.ttl_ms is not None
            and task.remote_created_at is not None
        ):
            remote_deadline = task.remote_created_at + timedelta(milliseconds=remote.ttl_ms)
            task.deadline_at = min(task.deadline_at, remote_deadline)

        if remote.status == PersistentAgentMCPTask.Status.COMPLETED:
            try:
                normalized = get_mcp_manager().normalize_mcp_task_result(task, remote.result)
            except (ValueError, TypeError, MCPTaskProtocolError) as exc:
                _terminalize_locked(
                    task,
                    status=PersistentAgentMCPTask.Status.FAILED,
                    message="The MCP task returned a malformed tool result.",
                    error={"message": str(exc)},
                )
                record_mcp_task_lifecycle("failed", task, reason="malformed_result")
                return
            _terminalize_locked(
                task,
                status=PersistentAgentMCPTask.Status.COMPLETED,
                message=remote.status_message,
                result=normalized,
            )
            record_mcp_task_lifecycle("completed", task)
            return

        if remote.status == PersistentAgentMCPTask.Status.FAILED:
            _terminalize_locked(
                task,
                status=PersistentAgentMCPTask.Status.FAILED,
                message=remote.status_message or "The remote MCP task failed.",
                error=remote.error,
            )
            record_mcp_task_lifecycle("failed", task)
            return

        if remote.status == PersistentAgentMCPTask.Status.CANCELLED:
            _terminalize_locked(
                task,
                status=PersistentAgentMCPTask.Status.CANCELLED,
                message=remote.status_message or "The remote MCP task was cancelled.",
            )
            record_mcp_task_lifecycle("cancelled", task)
            return

        if remote.status == PersistentAgentMCPTask.Status.INPUT_REQUIRED:
            task.status = PersistentAgentMCPTask.Status.INPUT_REQUIRED
            task.input_requests = remote.input_requests
            task.next_poll_at = None
            task.lease_token = None
            task.lease_expires_at = None
            _enqueue_wake_locked(task, now)
            task.save()
            record_mcp_task_lifecycle("input_required", task)
            return

        task.status = PersistentAgentMCPTask.Status.WORKING
        task.next_poll_at = min(
            now + timedelta(milliseconds=task.poll_interval_ms),
            task.deadline_at,
        )
        task.lease_token = None
        task.lease_expires_at = None
        task.save()
        delay = max((task.next_poll_at - now).total_seconds(), 0)
        transaction.on_commit(
            lambda: enqueue_mcp_task_poll(task_id, countdown=delay)
        )


def _cancel_remote_task(task: PersistentAgentMCPTask) -> None:
    try:
        get_mcp_manager().cancel_mcp_task_remote(task)
    except REMOTE_CANCEL_ERRORS:
        logger.info("Best-effort remote MCP task cancellation failed", exc_info=True)


def _finish_task(
    task_id: str,
    *,
    status: str,
    message: str,
    event: str,
    reason: Optional[str] = None,
) -> None:
    task_snapshot: Optional[PersistentAgentMCPTask] = None
    with transaction.atomic():
        task = (
            PersistentAgentMCPTask.objects.select_for_update()
            .select_related("agent", "agent__user", "agent__organization")
            .filter(pk=task_id)
            .first()
        )
        if task is None or task.terminal_at is not None:
            return
        task_snapshot = task
        _terminalize_locked(task, status=status, message=message)
        attributes = {"reason": reason} if reason else {}
        record_mcp_task_lifecycle(event, task, **attributes)
    if task_snapshot is not None:
        _cancel_remote_task(task_snapshot)


def _expire_task(task_id: str, *, message: str) -> None:
    _finish_task(
        task_id,
        status=PersistentAgentMCPTask.Status.EXPIRED,
        message=message,
        event="expired",
    )


def _cancel_task(task_id: str, *, message: str, reason: str) -> None:
    _finish_task(
        task_id,
        status=PersistentAgentMCPTask.Status.CANCELLED,
        message=message,
        event="cancelled",
        reason=reason,
    )


def _reconcile_missing_wake(task_id: str) -> None:
    with transaction.atomic():
        task = PersistentAgentMCPTask.objects.select_for_update().get(pk=task_id)
        is_deliverable = (
            task.terminal_at is not None
            or task.status == PersistentAgentMCPTask.Status.INPUT_REQUIRED
        )
        if not is_deliverable or task.wake_enqueued_at is not None:
            return
        _enqueue_wake_locked(task, timezone.now())
        task.save(update_fields=["wake_enqueued_at", "updated_at"])


@shared_task(name="api.tasks.poll_mcp_task")
def poll_mcp_task(task_id: str) -> None:
    if not settings.MCP_ASYNC_TASKS_ENABLED:
        _cancel_task(
            task_id,
            message="Outbound MCP task execution is disabled.",
            reason="feature_disabled",
        )
        return
    task, lease_token = _claim_task(task_id)
    if task is None or lease_token is None:
        return
    if timezone.now() >= task.deadline_at:
        _expire_task(task_id, message="The MCP task exceeded its allowed lifetime.")
        return

    owner = resolve_agent_owner(task.agent)
    if owner is not None and is_owner_execution_paused(owner):
        _cancel_task(
            task_id,
            message="The MCP task was cancelled because owner execution is paused.",
            reason="owner_paused",
        )
        return

    try:
        remote = get_mcp_manager().get_mcp_task_state(task)
    except (httpx.TimeoutException, TimeoutError) as exc:
        _schedule_retry(task_id, lease_token, str(exc))
        return
    except MCPTaskHTTPError as exc:
        if exc.retryable:
            _schedule_retry(task_id, lease_token, str(exc))
            return
        error_message = str(exc)
    except (httpx.HTTPError, MCPTaskProtocolError) as exc:
        error_message = str(exc)
    else:
        record_mcp_task_lifecycle("polled", task, remote_status=remote.status)
        _apply_remote_state(task_id, lease_token, remote)
        return

    with transaction.atomic():
        locked = PersistentAgentMCPTask.objects.select_for_update().get(pk=task_id)
        if locked.terminal_at is None and locked.lease_token == lease_token:
            _terminalize_locked(
                locked,
                status=PersistentAgentMCPTask.Status.FAILED,
                message="The MCP task could not be polled.",
                error={"message": error_message},
            )
            record_mcp_task_lifecycle("failed", locked, reason="poll_error")


@shared_task(name="api.tasks.reconcile_mcp_tasks")
def reconcile_mcp_tasks() -> int:
    now = timezone.now()
    batch_size = max(settings.MCP_ASYNC_TASK_RECONCILE_BATCH_SIZE, 1)
    if not settings.MCP_ASYNC_TASKS_ENABLED:
        task_ids = list(
            PersistentAgentMCPTask.objects.filter(terminal_at__isnull=True)
            .values_list("id", flat=True)[:batch_size]
        )
        for task_id in task_ids:
            _cancel_task(
                str(task_id),
                message="Outbound MCP task execution is disabled.",
                reason="feature_disabled",
            )
        return len(task_ids)

    missing_wake_ids = list(
        PersistentAgentMCPTask.objects.filter(wake_enqueued_at__isnull=True)
        .filter(
            Q(terminal_at__isnull=False)
            | Q(
                status=PersistentAgentMCPTask.Status.INPUT_REQUIRED,
                input_requests__isnull=False,
            )
        )
        .values_list("id", flat=True)[:batch_size]
    )
    for task_id in missing_wake_ids:
        _reconcile_missing_wake(str(task_id))

    active_tasks = list(
        PersistentAgentMCPTask.objects.filter(
            terminal_at__isnull=True,
            status__in=PersistentAgentMCPTask.ACTIVE_STATUSES,
        )
        .select_related("server_config", "agent", "agent__user", "agent__organization")
        .order_by("created_at")[:batch_size]
    )
    reconciled_ids = set()
    for task in active_tasks:
        if task.server_config is None or not task.server_config.is_active:
            _cancel_task(
                str(task.id),
                message="The MCP server was disabled or removed.",
                reason="server_unavailable",
            )
            reconciled_ids.add(task.id)
            continue
        owner = resolve_agent_owner(task.agent)
        if owner is not None and is_owner_execution_paused(owner):
            _cancel_task(
                str(task.id),
                message="The MCP task was cancelled because owner execution is paused.",
                reason="owner_paused",
            )
            reconciled_ids.add(task.id)

    expired_ids = list(
        PersistentAgentMCPTask.objects.filter(
            terminal_at__isnull=True,
            status__in=PersistentAgentMCPTask.ACTIVE_STATUSES,
            deadline_at__lte=now,
        )
        .exclude(id__in=reconciled_ids)
        .values_list("id", flat=True)[:batch_size]
    )
    for task_id in expired_ids:
        _expire_task(str(task_id), message="The MCP task exceeded its allowed lifetime.")

    remaining = max(batch_size - len(expired_ids), 0)
    due_ids = list(
        PersistentAgentMCPTask.objects.filter(
            terminal_at__isnull=True,
            next_poll_at__lte=now,
        )
        .exclude(
            status=PersistentAgentMCPTask.Status.INPUT_REQUIRED,
            input_requests__isnull=False,
        )
        .exclude(id__in=reconciled_ids)
        .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now))
        .values_list("id", flat=True)[:remaining]
    )
    for task_id in due_ids:
        poll_mcp_task.delay(str(task_id))
    return len(missing_wake_ids) + len(reconciled_ids) + len(expired_ids) + len(due_ids)


def cancel_active_mcp_tasks_for_server(server_config_id: str, *, reason: str) -> int:
    tasks = list(
        PersistentAgentMCPTask.objects.filter(
            server_config_id=server_config_id,
            terminal_at__isnull=True,
        ).select_related("agent", "agent__user", "agent__organization")
    )
    for task in tasks:
        _cancel_task(str(task.id), message=reason, reason="server_cleanup")
    return len(tasks)
