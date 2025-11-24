from __future__ import annotations
import logging
from typing import Any, Iterable, Optional, Tuple, Dict
from uuid import UUID
import json
import time

from api.models import (
    PersistentAgent,
    PersistentAgentMessage,
    EvalRunTask,
    EvalRun,
    CommsAllowlistEntry
)
from api.agent.comms.message_service import inject_internal_web_message
from api.agent.core.llm_utils import run_completion
from api.agent.events import AgentEventType, get_agent_event_stream
from api.evals.realtime import broadcast_task_update
from config.redis_client import get_redis_client
from api.agent.core.llm_config import get_llm_config, LLMNotConfiguredError
from django.utils import timezone

logger = logging.getLogger(__name__)

class AgentEventListener:
    """
    Lightweight, event-driven listener that reads the agent event stream.
    Designed to avoid polling and to tolerate events that were emitted just
    before the listener started (by filtering on start_time).
    """
    def __init__(self, agent_id: str, *, start_time: Optional[float] = None):
        self.agent_id = str(agent_id)
        self.stream_key = get_agent_event_stream(agent_id)
        self.start_time = start_time or time.time()
        self.redis = get_redis_client()
        self.last_id = "0-0"

    def __enter__(self) -> "AgentEventListener":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        # No resources to release; propagate exceptions if any
        return False

    def wait_for(
        self,
        event_type: AgentEventType | str,
        timeout: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """
        Block on the Redis stream for a matching event or until timeout.
        Returns the decoded event dict or None on timeout/error.
        """
        target_type = event_type.value if isinstance(event_type, AgentEventType) else str(event_type)
        deadline = time.time() + timeout

        while time.time() < deadline:
            remaining_ms = max(1, int((deadline - time.time()) * 1000))
            try:
                messages = self.redis.xread(
                    {self.stream_key: self.last_id},
                    count=50,
                    block=remaining_ms,
                )
            except Exception:
                logger.warning("Failed to read agent event stream for %s", self.agent_id, exc_info=True)
                return None

            if not messages:
                continue

            for _stream, entries in messages:
                for entry_id, raw_fields in entries:
                    self.last_id = entry_id
                    raw = raw_fields.get("data") or raw_fields.get(b"data")
                    if raw is None:
                        continue
                    try:
                        if isinstance(raw, bytes):
                            event = json.loads(raw.decode("utf-8"))
                        elif isinstance(raw, str):
                            event = json.loads(raw)
                        elif isinstance(raw, dict):
                            event = raw
                        else:
                            continue
                    except Exception:
                        continue

                    try:
                        ts_val = float(event.get("timestamp", 0))
                    except Exception:
                        ts_val = 0.0

                    # Ignore stale events that occurred before we started listening
                    if ts_val < self.start_time:
                        continue

                    if event.get("type") == target_type:
                        return event

        return None

class ScenarioExecutionTools:
    """
    Tools for scenarios to interact with the agent and record results.
    Intended to be used as a base class or mixin for EvalScenario.
    """

    def get_agent(self, agent_id: str) -> PersistentAgent:
        return PersistentAgent.objects.get(id=agent_id)
    
    def get_run(self, run_id: str) -> EvalRun:
        return EvalRun.objects.get(id=run_id)

    def inject_message(
        self, 
        agent_id: str, 
        body: str, 
        sender_user_id: int = -999, 
        attachments: Iterable[Any] = (),
        trigger_processing: bool = True,
        eval_run_id: str | None = None,
    ) -> PersistentAgentMessage:
        """
        Send a message to the agent as a web user.
        Automatically whitelists the sender to ensure the agent can reply.
        """
        msg, _ = inject_internal_web_message(
            agent_id=agent_id,
            body=body,
            sender_user_id=sender_user_id,
            attachments=attachments,
            trigger_processing=trigger_processing,
            eval_run_id=eval_run_id,
        )
        
        # Auto-whitelist the sender so the agent trusts this contact
        CommsAllowlistEntry.objects.get_or_create(
            agent_id=agent_id,
            channel=msg.from_endpoint.channel,
            address=msg.from_endpoint.address,
            defaults={
                "is_active": True,
            }
        )
        
        # Update agent's preferred contact to this new user so "Welcome" prompts target them
        agent = PersistentAgent.objects.get(id=agent_id)
        agent.preferred_contact_endpoint = msg.from_endpoint
        agent.save(update_fields=["preferred_contact_endpoint"])
        
        return msg

    def trigger_processing(self, agent_id: str, *, eval_run_id: str | None = None) -> None:
        """
        Manually trigger the agent's event processing loop.
        """
        # Import here to avoid circular imports at module level
        from api.agent.tasks import process_agent_events_task
        process_agent_events_task.delay(str(agent_id), eval_run_id=eval_run_id)

    def agent_event_listener(self, agent_id: str, *, start_time: Optional[float] = None) -> AgentEventListener:
        """
        Convenience helper to create an AgentEventListener with a start timestamp.
        """
        return AgentEventListener(agent_id, start_time=start_time)

    def record_task_result(
        self,
        run_id: str,
        task_sequence: Optional[int],
        status: str,
        observed_summary: str = "",
        expected_summary: str = "",
        artifacts: Dict[str, Any] = None,
        task_name: Optional[str] = None
    ) -> EvalRunTask:
        """
        Update or create a task result record.
        """
        artifacts = artifacts or {}
        
        if task_name:
            task_obj = EvalRunTask.objects.get(run_id=run_id, name=task_name)
        elif task_sequence is not None:
            task_obj, created = EvalRunTask.objects.get_or_create(
                run_id=run_id,
                sequence=task_sequence,
                defaults={
                    "name": f"Task {task_sequence}",
                    "assertion_type": "manual"
                }
            )
        else:
            raise ValueError("Must provide either task_sequence or task_name")
        
        task_obj.status = status
        if observed_summary:
            task_obj.observed_summary = observed_summary
        if expected_summary:
            task_obj.expected_summary = expected_summary

        now = timezone.now()
        if task_obj.started_at is None and status in (
            EvalRunTask.Status.RUNNING,
            EvalRunTask.Status.PASSED,
            EvalRunTask.Status.FAILED,
            EvalRunTask.Status.ERRORED,
            EvalRunTask.Status.SKIPPED,
        ):
            task_obj.started_at = now

        if status in (
            EvalRunTask.Status.PASSED,
            EvalRunTask.Status.FAILED,
            EvalRunTask.Status.ERRORED,
            EvalRunTask.Status.SKIPPED,
        ):
            task_obj.finished_at = now
            
        # Link artifacts if provided
        if "message" in artifacts:
            task_obj.first_message = artifacts["message"]
        if "step" in artifacts:
            task_obj.first_step = artifacts["step"]
        if "browser_task" in artifacts:
            task_obj.first_browser_task = artifacts["browser_task"]
            
        task_obj.save()

        try:
            broadcast_task_update(task_obj)
        except Exception:
            logger.debug("Broadcast task update failed", exc_info=True)

        return task_obj

    def llm_judge(
        self, 
        question: str, 
        context: str, 
        options: Iterable[str] = ("Yes", "No"),
        model: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """
        Ask an LLM to judge a context based on a question and a set of options.
        Uses tool calling to ensure structured output. Automatically falls back to
        the configured failover tier to pick the first available model when none
        (or only a model name) is provided. 
        
        Args:
            question: The specific question to answer.
            context: The context text to evaluate.
            options: A list of valid answer options (default: ["Yes", "No"]).
            model: Optional LLM model to use. If omitted, the configured default is used.
            params: Optional LLM parameters. If omitted, the configured default params are used.
            
        Returns:
            A tuple of (choice, reasoning). choice will be one of the strings in `options`.
        """
        options_list = list(options)
        
        tool_definition = {
            "type": "function",
            "function": {
                "name": "submit_judgment",
                "description": "Submit the final judgment for the evaluation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "choice": {
                            "type": "string",
                            "enum": options_list,
                            "description": "The selected answer from the valid options."
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Concise justification for the choice."
                        }
                    },
                    "required": ["choice", "reasoning"]
                }
            }
        }
        
        prompt = [
            {"role": "system", "content": "You are an impartial judge. Evaluate the context and answer the question by calling the `submit_judgment` tool."},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}\n\nValid Options: {', '.join(options_list)}"}
        ]

        configured_model: Optional[str] = None
        configured_params: Dict[str, Any] = {}

        if model is None or params is None:
            try:
                configured_model, configured_params = get_llm_config()
            except LLMNotConfiguredError as exc:
                # If a caller passed a model but no params, continue with empty params.
                if model is None:
                    logger.error("LLM judge missing configuration: %s", exc)
                    return "Error", "No LLM configuration available for judgment."
                configured_params = {}

        effective_model = model or configured_model
        if not effective_model:
            return "Error", "No LLM model available for judgment."

        safe_params = dict(params or configured_params or {})
        # Default to deterministic temperature unless the endpoint requires its own value.
        if safe_params.get("temperature") is None:
            safe_params["temperature"] = 0.0
        
        try:
            response = run_completion(
                model=effective_model,
                messages=prompt,
                tools=[tool_definition],
                tool_choice={"type": "function", "function": {"name": "submit_judgment"}},
                params=safe_params
            )
            
            tool_calls = response.choices[0].message.tool_calls
            if not tool_calls:
                 return "Error", "LLM did not call the judgment tool."
                 
            # We expect exactly one tool call since we forced it
            args = json.loads(tool_calls[0].function.arguments)
            return args.get("choice"), args.get("reasoning")
            
        except Exception as e:
            logger.error(f"LLM judge failed: {e}")
            return "Error", f"Exception during judgment: {str(e)}"

    def wait_for_event(
        self,
        agent_id: str,
        event_type: str,
        timeout: int = 30,
        start_time: Optional[float] = None,
        listener: Optional[AgentEventListener] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Block until a specific event type is received for the agent using the event stream.
        Returns the full event payload if received, None if timeout.
        If a listener is provided, it will be used (and advanced) to avoid duplicate reads.
        """
        effective_listener = listener or AgentEventListener(agent_id, start_time=start_time)
        return effective_listener.wait_for(event_type, timeout=timeout)

    def wait_for_idle(self, agent_id: str, timeout: int = 60) -> bool:
        """
        Wait until the agent emits PROCESSING_COMPLETE with 0 outstanding tasks.
        Returns True if idle state reached, False if timeout.
        """
        listener = AgentEventListener(agent_id, start_time=time.time())
        deadline = time.time() + timeout
        remaining = timeout

        while remaining > 0:
            event = listener.wait_for(AgentEventType.PROCESSING_COMPLETE, timeout=int(remaining))
            if not event:
                return False
            outstanding = int((event.get("payload") or {}).get("outstanding_tasks", 0) or 0)
            if outstanding == 0:
                return True
            remaining = max(0, deadline - time.time())
        return False

    def wait_for_agent_idle(self, agent_id: str, timeout: int = 60):
        """
        Return a context manager that waits for the agent to become idle after the block executes.
        Usage:
            with self.wait_for_agent_idle(agent_id):
                self.inject_message(...)
        """
        return WaitForIdleContext(agent_id, timeout)

class WaitForIdleContext:
    """
    Context manager that subscribes to agent events BEFORE the action,
    then waits for the agent to go idle AFTER the action.
    Eliminates race conditions in eager/fast execution environments.
    """
    def __init__(self, agent_id: str, timeout: int = 60):
        self.agent_id = agent_id
        self.timeout = timeout
        self.listener: Optional[AgentEventListener] = None

    def __enter__(self):
        self.listener = AgentEventListener(self.agent_id, start_time=time.time())
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            return False  # Propagate exception

        if not self.listener:
            return False

        deadline = time.time() + self.timeout
        remaining = self.timeout
        while remaining > 0:
            event = self.listener.wait_for(AgentEventType.PROCESSING_COMPLETE, timeout=int(remaining))
            if not event:
                break
            outstanding = int((event.get("payload") or {}).get("outstanding_tasks", 0) or 0)
            if outstanding == 0:
                return True  # Success
            remaining = max(0, deadline - time.time())

        logger.warning(f"Timeout waiting for agent {self.agent_id} to go idle.")
        return False # Do not suppress exceptions, but flow continues if no exception
