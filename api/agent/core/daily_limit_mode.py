from decimal import Decimal
from typing import Sequence


DAILY_LIMIT_MESSAGE_TOOL_NAMES = frozenset(
    {
        "send_email",
        "send_sms",
        "send_chat_message",
        "send_agent_message",
    }
)


def is_daily_limit_message_tool(tool_name: str | None) -> bool:
    return bool(tool_name and tool_name in DAILY_LIMIT_MESSAGE_TOOL_NAMES)


def is_daily_hard_limit_message_only_mode(daily_credit_state: dict | None) -> bool:
    if not isinstance(daily_credit_state, dict):
        return False

    hard_limit = daily_credit_state.get("hard_limit")
    if hard_limit is None:
        return False

    remaining = daily_credit_state.get("hard_limit_remaining")
    if remaining is None:
        try:
            used = daily_credit_state.get("used", Decimal("0"))
            if not isinstance(used, Decimal):
                used = Decimal(str(used))
            if not isinstance(hard_limit, Decimal):
                hard_limit = Decimal(str(hard_limit))
            remaining = hard_limit - used
        except Exception:
            return False

    try:
        if not isinstance(remaining, Decimal):
            remaining = Decimal(str(remaining))
        return remaining <= Decimal("0")
    except Exception:
        return False


def filter_tools_for_daily_limit_message_only_mode(tools: Sequence[dict]) -> list[dict]:
    filtered: list[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function_block = tool.get("function")
        if not isinstance(function_block, dict):
            continue
        if is_daily_limit_message_tool(function_block.get("name")):
            filtered.append(tool)
    return filtered
