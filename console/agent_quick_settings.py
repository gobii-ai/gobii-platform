from console.daily_credit import (
    build_agent_daily_credit_context,
    build_daily_credit_status,
    serialize_daily_credit_payload,
)


def build_agent_quick_settings_payload(agent, owner=None) -> dict:
    context = build_agent_daily_credit_context(agent, owner)
    return {
        "settings": {
            "dailyCredits": serialize_daily_credit_payload(context),
        },
        "status": {
            "dailyCredits": build_daily_credit_status(context),
        },
    }
