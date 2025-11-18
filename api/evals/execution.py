
import logging
from typing import Any, Iterable, Optional, Tuple, Dict
from uuid import UUID
import json

from api.models import (
    PersistentAgent,
    PersistentAgentMessage,
    EvalRunTask,
    EvalRun,
    BrowserUseAgentTask
)
from api.agent.comms.message_service import inject_internal_web_message
from api.agent.core.llm_utils import run_completion

logger = logging.getLogger(__name__)

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
        trigger_processing: bool = True
    ) -> PersistentAgentMessage:
        """
        Send a message to the agent as a web user.
        """
        msg, _ = inject_internal_web_message(
            agent_id=agent_id,
            body=body,
            sender_user_id=sender_user_id,
            attachments=attachments,
            trigger_processing=trigger_processing
        )
        return msg

    def trigger_processing(self, agent_id: str) -> None:
        """
        Manually trigger the agent's event processing loop.
        """
        # Import here to avoid circular imports at module level
        from api.agent.tasks import process_agent_events_task
        process_agent_events_task.delay(str(agent_id))

    def record_task_result(
        self,
        run_id: str,
        task_sequence: int,
        status: str,
        observed_summary: str = "",
        expected_summary: str = "",
        artifacts: Dict[str, Any] = None
    ) -> EvalRunTask:
        """
        Update or create a task result record.
        """
        artifacts = artifacts or {}
        
        task_obj, created = EvalRunTask.objects.get_or_create(
            run_id=run_id,
            sequence=task_sequence,
            defaults={
                "name": f"Task {task_sequence}",
                "assertion_type": "manual"
            }
        )
        
        task_obj.status = status
        if observed_summary:
            task_obj.observed_summary = observed_summary
        if expected_summary:
            task_obj.expected_summary = expected_summary
            
        # Link artifacts if provided
        if "message" in artifacts:
            task_obj.first_message = artifacts["message"]
        if "step" in artifacts:
            task_obj.first_step = artifacts["step"]
        if "browser_task" in artifacts:
            task_obj.first_browser_task = artifacts["browser_task"]
            
        task_obj.save()
        return task_obj

    def llm_judge(
        self, 
        question: str, 
        context: str, 
        options: Iterable[str] = ("Yes", "No"),
        model: str = "openai/gpt-4o"
    ) -> Tuple[str, str]:
        """
        Ask an LLM to judge a context based on a question and a set of options.
        Uses tool calling to ensure structured output. 
        
        Args:
            question: The specific question to answer.
            context: The context text to evaluate.
            options: A list of valid answer options (default: ["Yes", "No"]).
            model: The LLM model to use (default: "openai/gpt-4o").
            
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
        
        try:
            response = run_completion(
                model=model,
                messages=prompt,
                tools=[tool_definition],
                tool_choice={"type": "function", "function": {"name": "submit_judgment"}},
                params={"temperature": 0.0}
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