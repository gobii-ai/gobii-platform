"""Human input request tool for persistent agents."""

from typing import Any

from api.agent.comms.human_input_requests import MAX_HUMAN_INPUT_QUESTION_LENGTH, MAX_OPTION_COUNT, create_human_input_request, create_human_input_requests_batch
from api.models import CommsChannel, PersistentAgent
from .credential_solicitation import CREDENTIAL_SOLICITATION_ERROR_MESSAGE, request_solicits_credential_value


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
                "description": "Explicit recipient channel.",
            },
            "address": {
                "type": "string",
                "description": "Recipient address.",
            },
        },
        "required": ["channel", "address"],
    }
    option_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short user-facing label.",
            },
            "description": {
                "type": "string",
                "description": "One-sentence option detail.",
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
                "description": "Question text. Plain text only.",
            },
            "options": {
                "type": "array",
                "items": option_schema,
                "maxItems": 3,
                "description": "Broad intake requires 2-3 options total, including Other; never send a fourth. Otherwise omit or pass [] only when the blocker needs unconstrained text.",
            },
        },
        "required": ["question"],
    }

    return {
        "type": "function",
        "function": {
            "name": "request_human_input",
            "description": (
                "Tracked non-credential input/card; credentials use secure_credentials_request. "
                "Broad first assignment: orient with at most four read calls in two rounds (none if questions were requested first), then call with one evidence-informed, highest-leverage question and 2-3 real options total, including any Other option; never send a fourth. These options are mandatory even when evidence cannot identify an entity, so offer plausible interpretations or concrete next paths. Call this tool alone with empty response content: no kickoff/config/model/work, send tool, or prose substitute. Use the inbound channel. Web always retains the card; follow returned guidance to mirror its exact choices once to a separate preferred email/SMS. Email/SMS: send those numbered choices there. Ask another only if needed. Otherwise omit options for free-text blockers. "
                "Use message tools for non-blocking questions/answers. Include Other / I'll explain if needed. "
                "Do not use for preference surveys, timezone/channel/formatting, category example choices like which vendor/company, non-blocking lookback, or reversible defaults you can choose and disclose. "
                "Use for role-defining discovery when audience/scope/volume/success bounds materially change substantial ongoing first work; otherwise only if the user asks for targets/scope before setup or they block a recurring monitor. "
                f"Plain text only; max {MAX_HUMAN_INPUT_QUESTION_LENGTH} chars."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "maxLength": MAX_HUMAN_INPUT_QUESTION_LENGTH,
                        "description": "Question text. Plain text only.",
                    },
                    "options": {
                        "type": "array",
                        "items": option_schema,
                        "maxItems": 3,
                        "description": "Broad intake requires 2-3 options total, including Other; never send a fourth. Otherwise omit or pass [] only when the blocker needs unconstrained text.",
                    },
                    "requests": {
                        "type": "array",
                        "items": request_schema,
                        "description": "Multiple genuinely blocking requests with options; omit top-level question/options.",
                    },
                    "recipient": {
                        "description": "Optional explicit recipient; omit for the current implicit conversation target.",
                        **recipient_schema,
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": (
                            "REQUIRED; use true when you will send an email/SMS containing these questions or keep working; false if waiting."
                        ),
                    },
                },
                "required": ["will_continue_work"],
                "anyOf": [
                    {"required": ["question"]},
                    {"required": ["requests"]},
                ],
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
    if raw_requests is None:
        raw_requests = params.get("questions")
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
                    "options": options,
                }
            )

        if any(
            request_solicits_credential_value(request["question"], request["options"])
            for request in requests
        ):
            return {"status": "error", "message": CREDENTIAL_SOLICITATION_ERROR_MESSAGE}

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

    if request_solicits_credential_value(question, options):
        return {"status": "error", "message": CREDENTIAL_SOLICITATION_ERROR_MESSAGE}

    if options and len(options) > 3:
        return {
            "status": "error",
            "message": (
                "request_human_input is for one blocking decision, not preference surveys. "
                "Ask at most one concise question with up to 3 options, or choose a reasonable default and disclose it."
            ),
        }

    result = create_human_input_request(
        agent,
        question=question,
        raw_options=options or [],
        recipient=recipient,
    )
    if will_continue_work is True:
        result.pop("auto_sleep_ok", None)
    return result
