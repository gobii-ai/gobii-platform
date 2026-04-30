"""Human input request tool for persistent agents."""

from typing import Any

from api.agent.comms.human_input_requests import (
    MAX_HUMAN_INPUT_QUESTION_LENGTH,
    MAX_OPTION_COUNT,
    create_human_input_request,
    create_human_input_requests_batch,
)
from api.models import CommsChannel, PersistentAgent


def _coerce_optional_bool(raw: Any) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    return None


def get_request_human_input_tool() -> dict[str, Any]:
    """Return the human input request tool definition."""

    recipient_schema = {
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "enum": [CommsChannel.WEB, CommsChannel.EMAIL, CommsChannel.SMS],
                "description": "Channel for the explicitly targeted recipient.",
            },
            "address": {
                "type": "string",
                "description": "Recipient address for the selected channel.",
            },
        },
        "required": ["channel", "address"],
    }
    option_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short option label shown to the user.",
            },
            "description": {
                "type": "string",
                "description": "One-sentence explanation of the option.",
            },
        },
        "required": ["title", "description"],
    }
    request_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "maxLength": MAX_HUMAN_INPUT_QUESTION_LENGTH,
                "description": "Primary question or prompt for the user. Plain text only; do not use Markdown or HTML.",
            },
            "options": {
                "type": "array",
                "items": option_schema,
                "description": (
                    "Optional list of user-facing choices. Omit or pass [] for a free-text-only request."
                ),
            },
        },
        "required": ["question"],
    }

    return {
        "type": "function",
        "function": {
            "name": "request_human_input",
            "description": (
                "Create a tracked human-input request when you need the human to pick an option, "
                "answer a question, or provide open-ended feedback. If you pass options, the user "
                "can choose one OR reply in their own words. If you omit options, the user will "
                "reply with free text only. The request always appears in the web chat human input panel. "
                "In Planning Mode, planning questions must use this tool; questions sent only by "
                "chat, email, or SMS are not tracked and do not count. "
                "This tool does not send email or SMS by itself. If the target is email or SMS and you want "
                "to notify that channel, call this tool with will_continue_work=true and send a normal "
                "email or SMS that includes the exact question(s) and options. If you call send_email or "
                "send_sms in the same tool-call batch as request_human_input, that outbound message must "
                "already include the questions because this tool cannot inject them into another tool call. "
                "Do not send a bare notification like 'please answer the questions'; the recipient may not "
                "have web chat open. The user's reply on that channel will be processed as answers. "
                f"Questions must be plain text only, with no Markdown or HTML, and cannot exceed "
                f"{MAX_HUMAN_INPUT_QUESTION_LENGTH} characters. Keep questions concise and make sure "
                "it is only the question without extra fluff."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "maxLength": MAX_HUMAN_INPUT_QUESTION_LENGTH,
                        "description": "Primary question or prompt for the user. Plain text only; do not use Markdown or HTML.",
                    },
                    "options": {
                        "type": "array",
                        "items": option_schema,
                        "description": (
                            "Optional list of user-facing choices. Omit or pass [] for a free-text-only request."
                        ),
                    },
                    "requests": {
                        "type": "array",
                        "items": request_schema,
                        "description": (
                            "Optional list of multiple input requests to ask in one tool call. "
                            "When provided, omit the top-level question/options."
                        ),
                    },
                    "recipient": {
                        "description": (
                            "Optional explicit recipient target. When omitted, the request is sent "
                            "to the current implicit conversation target and can only be answered "
                            "by the agent owner, active org members, or collaborators."
                        ),
                        **recipient_schema,
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": (
                            "REQUIRED. true = you'll take another action in the same response or after creating this request; "
                            "use true when you will send an email/SMS containing these questions. "
                            "false = you're waiting for the user's answer and should stop after the request is visible/delivered."
                        ),
                    },
                },
                "required": ["will_continue_work"],
            },
        },
    }


def _normalize_request_options(raw_options: Any) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    if raw_options is None:
        return None, None
    if not isinstance(raw_options, list):
        return None, {
            "status": "error",
            "message": "Invalid parameter: options must be an array when provided.",
        }
    if raw_options and len(raw_options) > MAX_OPTION_COUNT:
        return None, {
            "status": "error",
            "message": f"Options cannot exceed {MAX_OPTION_COUNT} items.",
        }

    options: list[dict[str, Any]] = []
    for raw_option in raw_options or []:
        if not isinstance(raw_option, dict):
            return None, {
                "status": "error",
                "message": "Invalid option payload. Each option must be an object.",
            }
        option_title = str(raw_option.get("title") or "").strip()
        option_description = str(raw_option.get("description") or "").strip()
        if not option_title or not option_description:
            return None, {
                "status": "error",
                "message": "Each option must include title and description.",
            }
        options.append(
            {
                "title": option_title,
                "description": option_description,
            }
        )
    return options, None


def _normalize_recipient(raw_recipient: Any) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    if raw_recipient is None:
        return None, None
    if not isinstance(raw_recipient, dict):
        return None, {
            "status": "error",
            "message": "Invalid parameter: recipient must be an object when provided.",
        }

    channel = str(raw_recipient.get("channel") or "").strip().lower()
    address = str(raw_recipient.get("address") or "").strip()
    if channel not in {CommsChannel.WEB, CommsChannel.EMAIL, CommsChannel.SMS}:
        return None, {
            "status": "error",
            "message": "Recipient channel must be one of: web, email, sms.",
        }
    if not address:
        return None, {
            "status": "error",
            "message": "Recipient address is required when recipient is provided.",
        }

    return {
        "channel": channel,
        "address": address,
    }, None


def execute_request_human_input(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    """Create one or more tracked human input requests."""

    will_continue_work = _coerce_optional_bool(params.get("will_continue_work"))
    recipient, recipient_error = _normalize_recipient(params.get("recipient"))
    if recipient_error:
        return recipient_error

    raw_requests = params.get("requests")
    if raw_requests is not None:
        if not isinstance(raw_requests, list) or not raw_requests:
            return {
                "status": "error",
                "message": "Invalid parameter: requests must be a non-empty array when provided.",
            }

        requests: list[dict[str, Any]] = []
        for raw_request in raw_requests:
            if not isinstance(raw_request, dict):
                return {
                    "status": "error",
                    "message": "Each request must be an object.",
                }
            question = str(raw_request.get("question") or "").strip()
            if not question:
                return {
                    "status": "error",
                    "message": "Each request must include question.",
                }
            options, error = _normalize_request_options(raw_request.get("options"))
            if error:
                return error
            requests.append(
                {
                    "question": question,
                    "options": options or [],
                }
            )

        result = create_human_input_requests_batch(agent, requests=requests, recipient=recipient)
        if will_continue_work is True:
            result.pop("auto_sleep_ok", None)
        return result

    question = str(params.get("question") or "").strip()
    if not question:
        return {
            "status": "error",
            "message": "Missing required parameter: question.",
        }

    options, error = _normalize_request_options(params.get("options"))
    if error:
        return error

    result = create_human_input_request(
        agent,
        question=question,
        raw_options=options or [],
        recipient=recipient,
    )
    if will_continue_work is True:
        result.pop("auto_sleep_ok", None)
    return result
