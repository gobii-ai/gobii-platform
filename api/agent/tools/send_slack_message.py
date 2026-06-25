"""Agent tool for sending Slack messages through the native integration."""

import logging
from typing import Any, Dict

import requests
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from api.models import PersistentAgent
from api.services.slack_bot import SlackIntegrationError, send_channel_message

logger = logging.getLogger(__name__)


def get_send_slack_message_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "send_slack_message",
            "description": (
                "Send a text message to a Slack channel subscribed through the native Slack integration. "
                "The backend sends via chat.postMessage using this agent's name and public avatar URL as "
                "display-level message identity. Slack does not create separate mentionable bot users per agent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {
                        "type": "string",
                        "description": "Subscribed Slack channel ID.",
                    },
                    "message": {
                        "type": "string",
                        "description": (
                            "Text message body to send. Do not pass placeholders or tool-call/XML syntax; "
                            "it is sent literally. V1 supports text only, not Slack file uploads."
                        ),
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you'll take another action, false = you're done.",
                    },
                },
                "required": ["channel_id", "message", "will_continue_work"],
            },
        },
    }


def execute_send_slack_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    channel_id = str(params.get("channel_id") or "").strip()
    body = str(params.get("message") or "").strip()
    if not channel_id:
        return {"status": "error", "message": "channel_id is required."}
    if not body:
        return {"status": "error", "message": "message is required."}
    try:
        message = send_channel_message(agent, channel_id=channel_id, body=body)
        result: dict[str, Any] = {
            "status": "success",
            "message_id": str(message.id),
            "slack_ts": str((message.raw_payload or {}).get("slack_ts") or ""),
            "channel_id": channel_id,
        }
        if params.get("will_continue_work") is False:
            result["auto_sleep_ok"] = True
        return result
    except ObjectDoesNotExist:
        return {"status": "error", "message": "No active native Slack subscription was found for that channel."}
    except (SlackIntegrationError, ValidationError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    except requests.RequestException as exc:
        logger.warning("Native Slack send failed for agent %s: %s", agent.id, exc)
        return {"status": "error", "message": f"Slack request failed: {exc}"}
