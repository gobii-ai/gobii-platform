import logging
from typing import Any, Dict, Optional

import requests
from django.db import transaction
from django.utils import timezone
from requests import RequestException

from api.agent.tools.webhook_sender import USER_AGENT as DEFAULT_WEBHOOK_USER_AGENT
from api.models import BrowserUseAgentTask, BrowserUseAgentTaskStep
from api.proxy_selection import select_proxy_for_browser_task

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


def _select_proxy_for_webhook(task: BrowserUseAgentTask) -> tuple[dict[str, str] | None, Optional[str]]:
    """Pick a proxy for webhook delivery, preferring the task's agent proxy."""
    try:
        proxy_server = select_proxy_for_browser_task(task, allow_no_proxy_in_debug=False)
    except RuntimeError as exc:
        logger.error("Task %s webhook proxy selection failed: %s", task.id, exc)
        return None, str(exc)

    if not proxy_server:
        message = "No proxy server available for webhook delivery"
        logger.warning("Webhook proxy unavailable for task %s", task.id)
        return None, message

    proxy_url = proxy_server.proxy_url
    logger.info(
        "Using proxy %s:%s for webhook delivery on task %s",
        proxy_server.host,
        proxy_server.port,
        task.id,
    )
    return {"http": proxy_url, "https": proxy_url}, None


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
    proxies, proxy_error = _select_proxy_for_webhook(task)
    if proxy_error:
        error_message = proxy_error
        logger.warning("Skipping webhook delivery for task %s: %s", task.id, proxy_error)
    else:
        try:
            response = requests.post(
                task.webhook_url,
                json=payload,
                timeout=WEBHOOK_TIMEOUT_SECONDS,
                headers=WEBHOOK_HEADERS,
                proxies=proxies,
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
