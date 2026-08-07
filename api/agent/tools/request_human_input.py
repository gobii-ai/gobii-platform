"""Human input request tool for persistent agents."""

from typing import Any

from api.agent.comms.human_input_requests import MAX_HUMAN_INPUT_QUESTION_LENGTH, MAX_OPTION_COUNT, create_human_input_request, create_human_input_requests_batch
from api.models import CommsChannel, PersistentAgent
from .credential_solicitation import CREDENTIAL_SOLICITATION_ERROR_MESSAGE, request_solicits_credential_value


def _validation_error(message: str) -> dict[str, Any]:
    return {"status": "error", "message": message, "retryable": True}


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
                "minItems": 2,
                "maxItems": MAX_OPTION_COUNT,
                "description": f"Distinct choices, usually 2-3 and at most {MAX_OPTION_COUNT}.",
            },
        },
        "required": ["question", "options"],
    }

    return {
        "type": "function",
        "function": {
            "name": "request_human_input",
            "description": (
                "Use when required non-credential information is missing and work must wait; credentials use "
                "secure_credentials_request. Missing email/SMS recipient/detail is a free-text blocker: call once with "
                "will_continue_work=false; do not search, message, or inspect SQLite first, and a generic role is not a recipient. "
                "For guided intake, use one call after the single required lookup for a named subject; otherwise call directly. "
                "Use question/options for one decision "
                "or requests for several. Each request asks one question; its options are alternative answers to that question, "
                "not labels for other questions. Use 2-3 clear choices unless more are truly needed; do not bundle decisions "
                "or silently choose a material boundary. Choose and disclose sensible defaults for reversible details instead "
                "of asking a survey. Web cards stay pending; mirror them to email/SMS only when result guidance says to. "
                "Outside intake, omit options only for a genuine free-text blocker; use message tools for non-blocking questions. "
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
                        "minItems": 2,
                        "maxItems": MAX_OPTION_COUNT,
                        "description": f"Intake requires 2-{MAX_OPTION_COUNT} distinct choices; omit only for a free-text blocker.",
                    },
                    "requests": {
                        "type": "array",
                        "items": request_schema,
                        "description": "Several independent questions; omit top-level question/options.",
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
        return None, _validation_error("Invalid parameter: options must be an array when provided.")
    if len(raw_options) == 1:
        return None, _validation_error(
            "Options must be omitted for a free-text question or include at least 2 distinct choices."
        )
    if raw_options and len(raw_options) > MAX_OPTION_COUNT:
        return None, _validation_error(f"Options cannot exceed {MAX_OPTION_COUNT} items.")

    options: list[dict[str, Any]] = []
    for raw_option in raw_options or []:
        if not isinstance(raw_option, dict):
            return None, _validation_error("Invalid option payload. Each option must be an object.")
        option_title = str(raw_option.get("title") or "").strip()
        option_description = str(raw_option.get("description") or "").strip()
        if not option_title or not option_description:
            return None, _validation_error("Each option must include title and description.")
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
        return None, _validation_error("Invalid parameter: recipient must be an object when provided.")

    channel = str(raw_recipient.get("channel") or "").strip().lower()
    address = str(raw_recipient.get("address") or "").strip()
    if channel not in {CommsChannel.WEB, CommsChannel.EMAIL, CommsChannel.SMS}:
        return None, _validation_error("Recipient channel must be one of: web, email, sms.")
    if not address:
        return None, _validation_error("Recipient address is required when recipient is provided.")

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
            return _validation_error("Invalid parameter: requests must be a non-empty array when provided.")

        requests: list[dict[str, Any]] = []
        for raw_request in raw_requests:
            if not isinstance(raw_request, dict):
                return _validation_error("Each request must be an object.")
            question = str(raw_request.get("question") or "").strip()
            if not question:
                return _validation_error("Each request must include question.")
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
        return _validation_error("Missing required parameter: question.")

    options, error = _normalize_request_options(params.get("options"))
    if error:
        return error

    if request_solicits_credential_value(question, options):
        return {"status": "error", "message": CREDENTIAL_SOLICITATION_ERROR_MESSAGE}

    result = create_human_input_request(
        agent,
        question=question,
        raw_options=options or [],
        recipient=recipient,
    )
    if will_continue_work is True:
        result.pop("auto_sleep_ok", None)
    return result
