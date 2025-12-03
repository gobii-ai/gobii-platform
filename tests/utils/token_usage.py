from unittest.mock import MagicMock
import json


def make_completion_response(
    *,
    content: str = "Result",
    model: str = "test-model",
    provider: str | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    cached_tokens: int = 2,
    tool_names: list[str] | None = None,
    reasoning_content: str | None = None,
) -> MagicMock:
    """Return a mocked LiteLLM response with optional tool_calls."""
    response = MagicMock()
    response.choices = [MagicMock()]
    message = MagicMock(content=content)
    if reasoning_content is not None:
        message.reasoning_content = reasoning_content

    response.model = model
    if provider is not None:
        response.provider = provider

    if tool_names is not None:
        message.tool_calls = [
            {
                "function": {
                    "name": "enable_tools",
                    "arguments": json.dumps({"tool_names": tool_names}),
                }
            }
        ]
    response.choices[0].message = message

    usage_details = MagicMock(cached_tokens=cached_tokens)
    response.model_extra = {
        "usage": MagicMock(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            prompt_tokens_details=usage_details,
        )
    }
    return response
