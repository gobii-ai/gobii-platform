import logging
from typing import Any, Dict, Optional

import requests
from django.db import transaction
from django.utils import timezone
from requests import RequestException

from api.agent.tools.webhook_sender import USER_AGENT as DEFAULT_WEBHOOK_USER_AGENT
from api.models import BrowserUseAgentTask, BrowserUseAgentTaskStep

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {
    BrowserUseAgentTask.StatusChoices.COMPLETED,
    BrowserUseAgentTask.StatusChoices.FAILED,
    BrowserUseAgentTask.StatusChoices.CANCELLED,
}

WEBHOOK_TIMEOUT_SECONDS = 10
WEBHOOK_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": DEFAULT_WEBHOOK_USER_AGENT,
}


def _build_payload(task: BrowserUseAgentTask) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": str(task.id),
        "status": task.status,
        "agent_id": str(task.agent_id) if task.agent_id else None,
    }

    if task.status == BrowserUseAgentTask.StatusChoices.COMPLETED:
        result_step = BrowserUseAgentTaskStep.objects.filter(task=task, is_result=True).first()
        payload["result"] = result_step.result_value if result_step else None
    else:
        payload["result"] = None

    if task.status == BrowserUseAgentTask.StatusChoices.FAILED:
        if task.error_message:
            payload["error_message"] = task.error_message
    elif task.status == BrowserUseAgentTask.StatusChoices.CANCELLED:
        payload["message"] = "Task has been cancelled."

    return payload


def trigger_task_webhook(task: BrowserUseAgentTask) -> None:
    """
    Deliver the webhook notification for the given task if a webhook URL is configured.
    Ensures the webhook fires at most once per task lifecycle unless the tracking fields
    are manually cleared.
    """

    if not task.webhook_url:
        return

    if task.status not in TERMINAL_STATUSES:
        return

    if task.webhook_last_called_at:
        # Webhook already attempted; avoid duplicate notifications.
        return

    payload = _build_payload(task)
    delivered_at = timezone.now()
    status_code: Optional[int] = None
    error_message: Optional[str] = None

    try:
        response = requests.post(
            task.webhook_url,
            json=payload,
            timeout=WEBHOOK_TIMEOUT_SECONDS,
            headers=WEBHOOK_HEADERS,
        )
        status_code = response.status_code
        if not 200 <= status_code < 300:
            response_preview = (response.text or "")[:500]
            error_message = f"Received status {status_code}: {response_preview}".strip()
            logger.warning(
                "Webhook for task %s returned non-success status %s (%s)",
                task.id,
                status_code,
                response_preview,
            )
        else:
            logger.info("Webhook for task %s delivered successfully", task.id)
    except RequestException:
        logger.warning("Failed to deliver webhook for task %s", task.id, exc_info=True)
    except Exception:
        logger.exception("Unexpected error delivering webhook for task %s", task.id)

    # Persist delivery metadata without mutating other fields like status/updated_at.
    with transaction.atomic():
        BrowserUseAgentTask.objects.filter(pk=task.pk).update(
            webhook_last_called_at=delivered_at,
            webhook_last_status_code=status_code,
            webhook_last_error=error_message,
        )
