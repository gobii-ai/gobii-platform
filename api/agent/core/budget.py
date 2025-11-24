from __future__ import annotations

"""Shared step-budget and recursion-depth manager for persistent agents.

This module coordinates a single logical "cycle" of work that can span
multiple nested/background calls and Celery tasks. It persists state in Redis
so concurrent branches share a single step budget safely.

Terminology
-----------
- Cycle: a top-level agent processing window kicked off by a cron or message.
- Step: one iteration of the orchestrator loop (LLM + tool execution window).
- Branch: a nested follow-up chain, typically spawned by a background task.

Keys
----
- pa:budget:{agent_id}              -> hash(JSON-like fields)
- pa:budget:{agent_id}:steps        -> int counter
- pa:budget:{agent_id}:branches     -> hash(branch_id -> depth)
- pa:budget:{agent_id}:active       -> string(budget_id)

"""

import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from django.conf import settings

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)


# Tighter defaults to reduce eagerness and bound work per cycle
# These apply unless overridden via Django settings.
DEFAULT_MAX_STEPS: int = getattr(settings, "PA_MAX_STEPS_PER_CYCLE", 100)
DEFAULT_MAX_DEPTH: int = getattr(settings, "PA_MAX_RECURSION_DEPTH", 2)
# Keep budget context alive long enough to cover long‑running tasks
# Align with distributed lock auto_release_time (4h) by default
DEFAULT_TTL_SECONDS: int = getattr(settings, "PA_CYCLE_TTL_SECONDS", 14400)


def _key_budget(agent_id: str) -> str:
    return f"pa:budget:{agent_id}"


def _key_steps(agent_id: str) -> str:
    return f"pa:budget:{agent_id}:steps"


def _key_branches(agent_id: str) -> str:
    return f"pa:budget:{agent_id}:branches"


def _key_active(agent_id: str) -> str:
    return f"pa:budget:{agent_id}:active"


@dataclass(frozen=True)
class BudgetContext:
    agent_id: str
    budget_id: str
    branch_id: str
    depth: int
    max_steps: int
    max_depth: int
    eval_run_id: Optional[str] = None


class AgentBudgetManager:
    """Manages the cross-call step budget for a persistent agent."""

    @staticmethod
    def _to_str(val):
        if isinstance(val, (bytes, bytearray)):
            try:
                return val.decode("utf-8", "ignore")
            except Exception:
                return str(val)
        return val

    @staticmethod
    def _to_int(val, default: int) -> int:
        if val is None:
            return default
        if isinstance(val, (bytes, bytearray)):
            try:
                return int(val.decode("utf-8", "ignore"))
            except Exception:
                return default
        try:
            return int(val)
        except Exception:
            return default

    @staticmethod
    def find_or_start_cycle(
        *,
        agent_id: str,
        max_steps: Optional[int] = None,
        max_depth: Optional[int] = None,
    ) -> Tuple[str, int, int]:
        """Return (budget_id, max_steps, max_depth) for the active cycle.

        If no active cycle exists, start a new one.
        """
        redis = get_redis_client()
        active_key = _key_active(agent_id)
        budget_key = _key_budget(agent_id)
        steps_key = _key_steps(agent_id)
        branches_key = _key_branches(agent_id)

        # Try to reuse an active budget if present
        budget_id = AgentBudgetManager._to_str(redis.get(active_key))
        if budget_id:
            # Ensure the budget hash still exists; otherwise treat as missing
            if redis.exists(budget_key):
                # Read limits
                data = redis.hgetall(budget_key)
                max_steps_val = AgentBudgetManager._to_int(data.get("max_steps"), DEFAULT_MAX_STEPS)
                max_depth_val = AgentBudgetManager._to_int(data.get("max_depth"), DEFAULT_MAX_DEPTH)

                # Refresh TTLs
                ttl = DEFAULT_TTL_SECONDS
                redis.expire(budget_key, ttl)
                redis.expire(steps_key, ttl)
                redis.expire(branches_key, ttl)
                redis.expire(active_key, ttl)
                return str(budget_id), max_steps_val, max_depth_val

        # Otherwise, create a fresh cycle
        new_budget_id = str(uuid.uuid4())
        max_steps_val = int(max_steps) if max_steps is not None else DEFAULT_MAX_STEPS
        max_depth_val = int(max_depth) if max_depth is not None else DEFAULT_MAX_DEPTH

        pipe = redis.pipeline()
        pipe.hset(
            budget_key,
            mapping={
                "budget_id": new_budget_id,
                "max_steps": max_steps_val,
                "max_depth": max_depth_val,
                "status": "active",
            },
        )
        pipe.set(steps_key, 0)
        pipe.delete(branches_key)
        # Set active pointer
        pipe.set(active_key, new_budget_id)
        # TTLs
        for key in (budget_key, steps_key, branches_key, active_key):
            pipe.expire(key, DEFAULT_TTL_SECONDS)
        pipe.execute()

        return new_budget_id, max_steps_val, max_depth_val

    @staticmethod
    def close_cycle(*, agent_id: str, budget_id: str) -> None:
        """Mark the cycle closed and clear the active pointer if it matches."""
        redis = get_redis_client()
        budget_key = _key_budget(agent_id)
        active_key = _key_active(agent_id)
        branches_key = _key_branches(agent_id)

        pipe = redis.pipeline()
        # Only mark closed if the budget matches
        data = redis.hgetall(budget_key)
        stored_id = AgentBudgetManager._to_str(data.get("budget_id"))
        if stored_id == budget_id:
            pipe.hset(budget_key, "status", "closed")
            # Clear active pointer if pointing to this budget
            active_val = AgentBudgetManager._to_str(redis.get(active_key))
            if active_val == budget_id:
                pipe.delete(active_key)
            # Clean up branches when closing the cycle
            pipe.delete(branches_key)
        # Keep structures around briefly in case of late readers
        pipe.expire(budget_key, 60)
        pipe.execute()

    @staticmethod
    def create_branch(*, agent_id: str, budget_id: str, depth: int) -> str:
        """Register a new branch at the given depth and return its ID."""
        redis = get_redis_client()
        branches_key = _key_branches(agent_id)
        branch_id = str(uuid.uuid4())
        pipe = redis.pipeline()
        pipe.hset(branches_key, branch_id, depth)
        pipe.expire(branches_key, DEFAULT_TTL_SECONDS)
        pipe.execute()
        return branch_id

    @staticmethod
    def get_branch_depth(*, agent_id: str, branch_id: str) -> Optional[int]:
        redis = get_redis_client()
        val = redis.hget(_key_branches(agent_id), branch_id)
        if val is None:
            return None
        try:
            return AgentBudgetManager._to_int(val, 0)
        except Exception:
            return None

    @staticmethod
    def set_branch_depth(*, agent_id: str, branch_id: str, depth: int) -> None:
        redis = get_redis_client()
        branches_key = _key_branches(agent_id)
        redis.hset(branches_key, branch_id, max(0, int(depth)))
        # keep TTL fresh
        try:
            pipe = redis.pipeline()
            pipe.expire(branches_key, DEFAULT_TTL_SECONDS)
            pipe.execute()
        except Exception:
            pass

    @staticmethod
    def remove_branch(*, agent_id: str, branch_id: str) -> None:
        """Remove a specific branch from the branches hash."""
        redis = get_redis_client()
        branches_key = _key_branches(agent_id)
        try:
            redis.hdel(branches_key, branch_id)
            logger.debug("Removed branch %s for agent %s", branch_id, agent_id)
        except Exception as e:
            logger.warning("Failed to remove branch %s for agent %s: %s", branch_id, agent_id, e)

    @staticmethod
    def bump_branch_depth(*, agent_id: str, branch_id: str, delta: int) -> int:
        """Atomically adjust branch recursion depth (supports FakeRedis fallback)."""
        redis = get_redis_client()
        key = _key_branches(agent_id)
        try:
            new_val = int(redis.hincrby(key, branch_id, int(delta)))  # type: ignore[attr-defined]
        except Exception:
            # Fallback for clients without hincrby: do a safe read-modify-write
            current = AgentBudgetManager.get_branch_depth(agent_id=agent_id, branch_id=branch_id) or 0
            new_val = max(0, int(current) + int(delta))
            redis.hset(key, branch_id, new_val)
        # Clamp to >= 0
        if new_val < 0:
            new_val = 0
            redis.hset(key, branch_id, 0)
        try:
            pipe = redis.pipeline()
            pipe.expire(key, DEFAULT_TTL_SECONDS)  # branches hash TTL
            pipe.expire(_key_budget(agent_id), DEFAULT_TTL_SECONDS)  # budget hash TTL
            pipe.expire(_key_active(agent_id), DEFAULT_TTL_SECONDS)  # active pointer TTL
            pipe.expire(_key_steps(agent_id), DEFAULT_TTL_SECONDS)  # steps counter TTL
            pipe.execute()
        except Exception:
            pass
        return new_val

    @staticmethod
    def try_consume_step(*, agent_id: str, max_steps: int) -> Tuple[bool, int]:
        """Atomically consume one step from the shared budget.

        Returns (consumed, new_steps_used). When not consumed, steps_used is the
        current value.
        """
        redis = get_redis_client()
        steps_key = _key_steps(agent_id)
        budget_key = _key_budget(agent_id)
        branches_key = _key_branches(agent_id)
        active_key = _key_active(agent_id)

        # Lua script to check-then-incr if under max
        script = (
            "local v = redis.call('GET', KEYS[1]) \n"
            "if not v then v = '0' end \n"
            "local n = tonumber(v) \n"
            "local max = tonumber(ARGV[1]) \n"
            "if n >= max then return {0, n} end \n"
            "n = n + 1 \n"
            "redis.call('SET', KEYS[1], n) \n"
            "return {1, n}"
        )

        res = redis.eval(script, 1, steps_key, max_steps)
        # res = [consumed_flag, new_value]
        try:
            consumed = bool(int(res[0]))
            new_val = int(res[1])
        except Exception:
            # Fallback: don't consume when something unexpected happens
            consumed = False
            try:
                current = redis.get(steps_key)
                new_val = int(current) if current is not None else 0
            except Exception:
                new_val = 0

        # Keep TTLs fresh on activity (steps, branches, budget, active)
        try:
            pipe = redis.pipeline()
            for key in (steps_key, branches_key, budget_key, active_key):
                pipe.expire(key, DEFAULT_TTL_SECONDS)
            pipe.execute()
        except Exception:
            # Non-fatal; TTLs will eventually expire
            pass
        return consumed, new_val

    @staticmethod
    def get_steps_used(*, agent_id: str) -> int:
        redis = get_redis_client()
        v = redis.get(_key_steps(agent_id))
        try:
            return AgentBudgetManager._to_int(v, 0)
        except Exception:
            return 0

    @staticmethod
    def get_total_outstanding_work(*, agent_id: str) -> int:
        """
        Return the total number of active branches (outstanding background tasks).
        Sums the depth/refcounts of all active branches in the branches hash.
        """
        redis = get_redis_client()
        branches_key = _key_branches(agent_id)
        try:
            data = redis.hgetall(branches_key)
            if not data:
                return 0
            
            total = 0
            for val in data.values():
                try:
                    # Handle both string and bytes from redis
                    count = AgentBudgetManager._to_int(val, 0)
                    # Only count positive depths as outstanding work
                    if count > 0:
                        total += count
                except Exception:
                    pass
            return total
        except Exception:
            logger.warning("Failed to get total outstanding work for agent %s", agent_id, exc_info=True)
            return 0

    @staticmethod
    def get_limits(*, agent_id: str) -> Tuple[int, int]:
        """Return (max_steps, max_depth) for the agent's current cycle.

        Falls back to defaults if missing.
        """
        redis = get_redis_client()
        data = redis.hgetall(_key_budget(agent_id))
        max_steps_val = AgentBudgetManager._to_int(data.get("max_steps"), DEFAULT_MAX_STEPS)
        max_depth_val = AgentBudgetManager._to_int(data.get("max_depth"), DEFAULT_MAX_DEPTH)
        return max_steps_val, max_depth_val

    @staticmethod
    def get_cycle_status(*, agent_id: str) -> Optional[str]:
        """Return the current budget status string (e.g., 'active', 'closed') if available."""
        redis = get_redis_client()
        data = redis.hgetall(_key_budget(agent_id))
        if not data:
            return None
        return AgentBudgetManager._to_str(data.get("status"))

    @staticmethod
    def get_active_budget_id(*, agent_id: str) -> Optional[str]:
        """Return the budget_id recorded in the budget hash for the agent's current cycle.

        Note: This reflects the ID in the budget hash; if the hash has expired or been
        closed and evicted, this will return None.
        """
        redis = get_redis_client()
        v = redis.hget(_key_budget(agent_id), "budget_id")
        return AgentBudgetManager._to_str(v) if v is not None else None


# Lightweight execution-context helpers (thread-local via contextvars)
import contextvars

_ctx_var: contextvars.ContextVar[Optional[BudgetContext]] = contextvars.ContextVar(
    "pa_budget_context", default=None
)


def set_current_context(ctx: BudgetContext | None) -> None:
    _ctx_var.set(ctx)


def get_current_context() -> Optional[BudgetContext]:
    return _ctx_var.get()
