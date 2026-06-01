"""Credit isolation helpers for eval execution."""

EVAL_EXECUTION_ENVIRONMENT = "eval"


def is_eval_credit_exempt_agent(agent) -> bool:
    return getattr(agent, "execution_environment", None) == EVAL_EXECUTION_ENVIRONMENT


def is_eval_credit_exempt_context(*, agent=None, eval_run_id=None) -> bool:
    return bool(eval_run_id) or (agent is not None and is_eval_credit_exempt_agent(agent))


def is_eval_credit_exempt_step(step) -> bool:
    agent = getattr(step, "agent", None)
    return is_eval_credit_exempt_context(agent=agent, eval_run_id=getattr(step, "eval_run_id", None))
