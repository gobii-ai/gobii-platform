"""
Webhook sender tool for persistent agents.

Provides a tool definition and execution helper that lets agents trigger
pre-configured outbound webhooks with structured JSON payloads.
"""

import logging
from typing import Any, Dict

import requests
from requests import RequestException

from ...models import PersistentAgent, PersistentAgentWebhook

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


def execute_send_webhook_event(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the send_webhook_event tool."""
    webhook_id = params.get("webhook_id")
    payload = params.get("payload")
    headers = _coerce_headers(params.get("headers"))

    if not webhook_id or not isinstance(webhook_id, str):
        return {"status": "error", "message": "Missing or invalid webhook_id parameter."}

    if not isinstance(payload, dict):
        return {"status": "error", "message": "Payload must be a JSON object."}

    try:
        webhook = agent.webhooks.get(id=webhook_id)
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
        list(payload.keys()),
    )

    try:
        response = requests.post(
            webhook.url,
            json=body,
            headers=request_headers,
            timeout=DEFAULT_TIMEOUT_SECONDS,
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
        webhook.record_delivery(status_code=None, error_message=error_message)
        return {
            "status": "error",
            "message": f"Webhook request failed: {error_message}",
            "webhook_id": str(webhook.id),
            "webhook_name": webhook.name,
        }

    if 200 <= status_code < 300:
        webhook.record_delivery(status_code=status_code, error_message="")
        return {
            "status": "success",
            "message": f"Delivered payload to webhook '{webhook.name}' (status {status_code}).",
            "webhook_id": str(webhook.id),
            "webhook_name": webhook.name,
            "response_status": status_code,
            "response_preview": response_preview,
        }

    webhook.record_delivery(status_code=status_code, error_message=response_preview)
    return {
        "status": "error",
        "message": f"Webhook responded with status {status_code}.",
        "webhook_id": str(webhook.id),
        "webhook_name": webhook.name,
        "response_status": status_code,
        "response_preview": response_preview,
    }
