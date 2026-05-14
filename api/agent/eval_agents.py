"""Helpers for behavior that should differ for eval-only agents."""

from api.models import PersistentAgent

EVAL_EXECUTION_ENVIRONMENT = "eval"


def is_eval_agent(agent: PersistentAgent) -> bool:
    return getattr(agent, "execution_environment", None) == EVAL_EXECUTION_ENVIRONMENT


__all__ = ["EVAL_EXECUTION_ENVIRONMENT", "is_eval_agent"]
