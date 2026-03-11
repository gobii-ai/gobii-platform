"""Human input request tool for persistent agents."""

from typing import Any

from api.agent.comms.human_input_requests import MAX_OPTION_COUNT, create_human_input_request
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
                    "title": {
                        "type": "string",
                        "description": "Short title for the request shown prominently to the user.",
                    },
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
                "required": ["title", "question"],
            },
        },
    }


def execute_request_human_input(agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    """Create and send a human input request."""

    title = str(params.get("title") or "").strip()
    question = str(params.get("question") or "").strip()
    raw_options = params.get("options")

    if not title or not question:
        return {
            "status": "error",
            "message": "Missing required parameters: title and question.",
        }

    if raw_options is not None and not isinstance(raw_options, list):
        return {
            "status": "error",
            "message": "Invalid parameter: options must be an array when provided.",
        }
    if raw_options and len(raw_options) > MAX_OPTION_COUNT:
        return {
            "status": "error",
            "message": f"Options cannot exceed {MAX_OPTION_COUNT} items.",
        }

    options: list[dict[str, Any]] = []
    for raw_option in raw_options or []:
        if not isinstance(raw_option, dict):
            return {
                "status": "error",
                "message": "Invalid option payload. Each option must be an object.",
            }
        option_title = str(raw_option.get("title") or "").strip()
        option_description = str(raw_option.get("description") or "").strip()
        if not option_title or not option_description:
            return {
                "status": "error",
                "message": "Each option must include title and description.",
            }
        options.append(
            {
                "title": option_title,
                "description": option_description,
            }
        )

    return create_human_input_request(
        agent,
        title=title,
        question=question,
        raw_options=options,
    )
