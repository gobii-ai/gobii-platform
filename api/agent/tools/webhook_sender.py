"""
Webhook sender tool for persistent agents.

Provides a tool definition and execution helper that lets agents trigger
pre-configured outbound webhooks with structured JSON payloads.
"""

import logging
from typing import Any, Dict, Iterable

import requests
from requests import RequestException
from django.core.exceptions import ValidationError

from ...models import PersistentAgent, PersistentAgentWebhook
from ...proxy_selection import select_proxy_for_persistent_agent, select_proxies_for_webhook
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15
USER_AGENT = "Gobii-AgentWebhook/1.0"


def get_send_webhook_tool() -> Dict[str, Any]:
    """Return the send_webhook_event tool definition exposed to the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "send_webhook_event",
            "description": (
                "Send a JSON payload to one of your configured outbound webhooks. "
                "You MUST provide the exact `webhook_id` from your context. "
                "Payloads should be concise, purpose-built JSON objects for the target system. "
                "Do NOT include secrets unless the user explicitly instructs you to."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "webhook_id": {
                        "type": "string",
                        "description": "The ID of the webhook to trigger (listed in your context).",
                    },
                    "payload": {
                        "type": "object",
                        "description": "JSON payload to deliver to the webhook endpoint.",
                    },
                    "headers": {
                        "type": "object",
                        "description": (
                            "Optional HTTP headers to include in the request. Keys and values must be strings."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "Set false to STOP when: all kanban cards are done AND you've sent your final report. Omitting defaults to continue. Always set false on your final tool call.",
                    },
                },
                "required": ["webhook_id", "payload"],
            },
        },
    }


def _coerce_headers(raw_headers: Any) -> Dict[str, str]:
    """Return a sanitized headers dictionary."""
    if not isinstance(raw_headers, dict):
        return {}

    safe_headers: Dict[str, str] = {}
    for key, value in raw_headers.items():
        if not isinstance(key, str):
            continue
        if not isinstance(value, str):
            continue
        safe_headers[key.strip()] = value
    return safe_headers


def _track_webhook_attempt(
    agent: PersistentAgent,
    webhook: PersistentAgentWebhook,
    *,
    result: str,
    status_code: int | None,
    error_message: str | None,
    payload_keys: Iterable[str],
    custom_header_count: int,
) -> None:
    """Emit analytics for webhook tool executions."""

    if not agent.user_id:
        return

    payload_key_list = list(payload_keys or [])
    props = {
        'agent_id': str(agent.id),
        'agent_name': agent.name,
        'webhook_id': str(webhook.id),
        'webhook_name': webhook.name,
        'result': result,
        'response_status_code': status_code,
        'payload_key_count': len(payload_key_list),
        'custom_header_count': custom_header_count,
        'timeout_seconds': DEFAULT_TIMEOUT_SECONDS,
    }

    if payload_key_list:
        props['payload_keys'] = payload_key_list
    if error_message:
        props['error_message'] = (error_message or '')[:200]

    props = Analytics.with_org_properties(props, organization=agent.organization)
    Analytics.track_event(
        user_id=agent.user_id,
        event=AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_TRIGGERED,
        source=AnalyticsSource.AGENT,
        properties=props.copy(),
    )


def _record_delivery_attempt(
    agent: PersistentAgent,
    webhook: PersistentAgentWebhook,
    *,
    result: str,
    status_code: int | None,
    error_message: str | None,
    payload_keys: Iterable[str],
    custom_header_count: int,
) -> None:
    """Persist delivery attempt metadata and track analytics."""
    webhook.record_delivery(status_code=status_code, error_message=error_message or "")
    _track_webhook_attempt(
        agent,
        webhook,
        result=result,
        status_code=status_code,
        error_message=error_message,
        payload_keys=payload_keys,
        custom_header_count=custom_header_count,
    )


def _build_webhook_response(
    *,
    status: str,
    webhook: PersistentAgentWebhook,
    message: str,
    status_code: int | None = None,
    response_preview: str | None = None,
) -> Dict[str, Any]:
    """Standardized response structure for webhook attempts."""
    response: Dict[str, Any] = {
        "status": status,
        "message": message,
        "webhook_id": str(webhook.id),
        "webhook_name": webhook.name,
    }
    if status_code is not None:
        response["response_status"] = status_code
    if response_preview is not None:
        response["response_preview"] = response_preview
    return response


def execute_send_webhook_event(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the send_webhook_event tool."""
    webhook_id = params.get("webhook_id")
    payload = params.get("payload")
    headers = _coerce_headers(params.get("headers"))
    will_continue_work_raw = params.get("will_continue_work", None)
    if will_continue_work_raw is None:
        will_continue_work = None
    elif isinstance(will_continue_work_raw, bool):
        will_continue_work = will_continue_work_raw
    elif isinstance(will_continue_work_raw, str):
        will_continue_work = will_continue_work_raw.lower() == "true"
    else:
        will_continue_work = None

    if not webhook_id or not isinstance(webhook_id, str):
        return {"status": "error", "message": "Missing or invalid webhook_id parameter."}

    if not isinstance(payload, dict):
        return {"status": "error", "message": "Payload must be a JSON object."}
    payload_keys = [str(key) for key in payload.keys()]
    custom_header_count = len(headers)

    try:
        webhook = agent.webhooks.get(id=webhook_id)
    except (ValidationError, ValueError):
        logger.warning("Agent %s supplied invalid webhook id %s", agent.id, webhook_id)
        return {"status": "error", "message": "Webhook not found for this agent."}
    except PersistentAgentWebhook.DoesNotExist:
        logger.warning("Agent %s attempted to call unknown webhook %s", agent.id, webhook_id)
        return {"status": "error", "message": "Webhook not found for this agent."}

    body = {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "webhook_id": str(webhook.id),
        "webhook_name": webhook.name,
        "payload": payload,
    }

    request_headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    request_headers.update(headers)

    logger.info(
        "Agent %s sending webhook '%s' (%s) payload keys=%s",
        agent.id,
        webhook.name,
        webhook.id,
        payload_keys,
    )

    proxies, proxy_error = select_proxies_for_webhook(
        agent,
        select_proxy_for_persistent_agent,
        log_context=f"agent {agent.id}",
    )
    if proxy_error:
        _record_delivery_attempt(
            agent,
            webhook,
            result="proxy_unavailable",
            status_code=None,
            error_message=proxy_error,
            payload_keys=payload_keys,
            custom_header_count=custom_header_count,
        )
        return _build_webhook_response(
            status="error",
            webhook=webhook,
            message=f"Webhook request failed: {proxy_error}",
        )

    try:
        response = requests.post(
            webhook.url,
            json=body,
            headers=request_headers,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            proxies=proxies,
        )
        status_code = response.status_code
        response_preview = (response.text or "")[:500]
    except RequestException as exc:
        error_message = str(exc)
        logger.warning(
            "Agent %s webhook '%s' (%s) failed: %s",
            agent.id,
            webhook.name,
            webhook.id,
            error_message,
        )
        _record_delivery_attempt(
            agent,
            webhook,
            result="request_error",
            status_code=None,
            error_message=error_message,
            payload_keys=payload_keys,
            custom_header_count=custom_header_count,
        )
        return _build_webhook_response(
            status="error",
            webhook=webhook,
            message=f"Webhook request failed: {error_message}",
        )

    if 200 <= status_code < 300:
        _record_delivery_attempt(
            agent,
            webhook,
            result="success",
            status_code=status_code,
            error_message=None,
            payload_keys=payload_keys,
            custom_header_count=custom_header_count,
        )
        response = _build_webhook_response(
            status="success",
            webhook=webhook,
            message=f"Delivered payload to webhook '{webhook.name}' (status {status_code}).",
            status_code=status_code,
            response_preview=response_preview,
        )
        if will_continue_work is False:
            response["auto_sleep_ok"] = True
        return response

    _record_delivery_attempt(
        agent,
        webhook,
        result="http_error",
        status_code=status_code,
        error_message=response_preview,
        payload_keys=payload_keys,
        custom_header_count=custom_header_count,
    )
    return _build_webhook_response(
        status="error",
        webhook=webhook,
        message=f"Webhook responded with status {status_code}.",
        status_code=status_code,
        response_preview=response_preview,
    )
