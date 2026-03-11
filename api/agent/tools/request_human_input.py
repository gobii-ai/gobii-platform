"""Human input request tool for persistent agents."""

from typing import Any

from api.agent.comms.human_input_requests import (
    MAX_OPTION_COUNT,
    create_human_input_request,
    create_human_input_requests_batch,
)
from api.models import PersistentAgent


def get_request_human_input_tool() -> dict[str, Any]:
    """Return the human input request tool definition."""

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
                "description": "Primary question or prompt for the user.",
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
                "Ask the user for input. Use this when you need the human to pick an option, "
                "answer a question, or provide open-ended feedback. If you pass options, the user "
                "can choose one OR reply in their own words. If you omit options, the user will "
                "reply with free text only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Primary question or prompt for the user.",
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
                },
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


def execute_request_human_input(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    """Create and send one or more human input requests."""

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

        return create_human_input_requests_batch(agent, requests=requests)

    question = str(params.get("question") or "").strip()
    if not question:
        return {
            "status": "error",
            "message": "Missing required parameter: question.",
        }

    options, error = _normalize_request_options(params.get("options"))
    if error:
        return error

    return create_human_input_request(
        agent,
        question=question,
        raw_options=options or [],
    )
