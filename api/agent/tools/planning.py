import re
from typing import Any, Dict

from api.models import PersistentAgent
from api.services.agent_planning import MAX_RUNTIME_CHARTER_CHARS, complete_agent_planning


_BLOCKING_DEPENDENCY_RE = re.compile(
    r"\b(?:"
    r"(?:cannot|can't|unable to|no (?:work|setup|execution|processing)|nothing can)\b.{0,60}\b(?:begin|proceed|start|run)\b.{0,30}\b(?:until|before)\b|"
    r"(?:work|setup|execution|processing)\b.{0,20}\b(?:blocked|on hold)\b.{0,30}\b(?:until|pending|for)\b|"
    r"waiting for\b.{0,60}(?:\bbefore\b|$)|"
    r"\bon hold pending\b|"
    r"\bmust be (?:answered|clarified|chosen|decided|selected)\b.{0,30}\bbefore\b"
    r")",
    re.IGNORECASE,
)
_HUMAN_DEPENDENCY_RE = re.compile(
    r"\b(?:"
    r"you(?:r)?\b.{0,20}\b(?:answer|clarif(?:y|ication)|choose|choice|decide|decision|input|select|selection)|"
    r"(?:answer|clarification|choice|decision|input|selection)\b.{0,12}\bfrom you\b|"
    r"(?:user|human)\b.{0,12}\b(?:answer|clarification|choice|decision|input|selection)\b"
    r")",
    re.IGNORECASE,
)
_NO_HUMAN_DEPENDENCY_RE = re.compile(
    r"\b(?:no (?:user|human) (?:input|action) (?:is )?(?:needed|required)|"
    r"(?:not|without) (?:user|human) (?:input|action)|"
    r"not (?:an? )?(?:answer|clarification|decision)|"
    r"arrive automatically)\b",
    re.IGNORECASE,
)


def _plan_has_unresolved_blocking_input(full_plan: str) -> bool:
    clauses = re.split(r"(?<=[.!?;])\s+|\n+", full_plan)
    for clause in clauses:
        if _NO_HUMAN_DEPENDENCY_RE.search(clause):
            continue
        if _BLOCKING_DEPENDENCY_RE.search(clause) and _HUMAN_DEPENDENCY_RE.search(clause):
            return True
    return False


def get_end_planning_tool() -> Dict[str, Any]:
    """Return the planning-mode completion tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "end_planning",
            "description": (
                "Finish planning when scope, outcome, constraints, and assumptions are clear. For a clear one-off "
                "or execute-now request, call this before research or delivery. For a named integration, call "
                "search_tools(provider) at most once. Tool/delivery availability is an execution prerequisite, "
                "not a planning blocker. Request tracked input only for genuinely blocking user decisions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "full_plan": {
                        "type": "string",
                        "maxLength": MAX_RUNTIME_CHARTER_CHARS,
                        "description": f"Decision-complete runtime charter; at most {MAX_RUNTIME_CHARTER_CHARS} characters.",
                    },
                    "schedule": {
                        "type": ["string", "null"],
                        "description": (
                            "Ongoing roles: cron or `@every`. Local wall times require a "
                            "`CRON_TZ=<saved/requested IANA zone> ` prefix; otherwise use UTC. "
                            "Omit or pass null to preserve the schedule; use clear_schedule to disable it."
                        ),
                    },
                    "clear_schedule": {
                        "type": "boolean",
                        "description": "True only when the user explicitly asked to disable recurrence.",
                    },
                },
                "required": ["full_plan"],
            },
        },
    }


def execute_end_planning(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the end_planning tool for a persistent agent."""
    full_plan = params.get("full_plan")
    if not isinstance(full_plan, str) or not full_plan.strip():
        return {"status": "error", "message": "Missing or invalid required parameter: full_plan"}
    if _plan_has_unresolved_blocking_input(full_plan):
        return {
            "status": "error",
            "retryable": False,
            "message": (
                "Planning cannot end while the brief says execution is blocked on an unresolved scope decision. "
                "Use request_human_input now, or replace reversible unknowns with explicit defaults."
            ),
        }

    try:
        planning_kwargs = {
            name: params[name]
            for name in ("schedule", "clear_schedule")
            if name in params
        }
        updated_agent = complete_agent_planning(agent, full_plan, **planning_kwargs)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    from console.agent_chat.signals import emit_agent_planning_state_update

    emit_agent_planning_state_update(updated_agent)

    return {
        "status": "ok",
        "message": "Planning completed.",
        "planning_state": PersistentAgent.PlanningState.COMPLETED,
        "schedule": updated_agent.schedule,
    }
