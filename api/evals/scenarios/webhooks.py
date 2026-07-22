import json

from api.agent.system_skills.defaults import WEBHOOKS_SYSTEM_SKILL_KEY
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.evals.tool_params import resolved_tool_param
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentInboundWebhook,
    PersistentAgentSystemSkillState,
    PersistentAgentToolCall,
    PersistentAgentWebhook,
)
from api.services.agent_webhooks import build_inbound_webhook_url


WEBHOOKS_SUITE_SLUG = "webhooks"
WEBHOOK_NATIVE_INBOUND_PROVIDER_SETUP = "webhook_native_inbound_provider_setup"
WEBHOOK_OUTBOUND_CONFIGURE_AND_SEND = "webhook_outbound_configure_and_send"
WEBHOOK_EXPLICIT_PIPEDREAM_ALLOWED = "webhook_explicit_pipedream_allowed"
WEBHOOK_SCENARIO_SLUGS = (
    WEBHOOK_NATIVE_INBOUND_PROVIDER_SETUP,
    WEBHOOK_OUTBOUND_CONFIGURE_AND_SEND,
    WEBHOOK_EXPLICIT_PIPEDREAM_ALLOWED,
)

WEBHOOK_TOOL_NAMES = {
    "manage_inbound_webhooks",
    "manage_outbound_webhooks",
    "send_webhook_event",
}


class WebhookScenarioBase(EvalScenario, ScenarioExecutionTools):
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_behavior", assertion_type="exact_match"),
    ]
    tier = "extended"
    category = "native_integrations"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("webhooks", "system_skill", "real_harness", "tool_choice")
    prompt = ""
    mock_config: dict[str, object] = {}
    needs_http_request = False

    def _prepare_agent(self, agent_id: str) -> PersistentAgent:
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Help configure integrations and react to provider events.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        agent = PersistentAgent.objects.get(id=agent_id)
        PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name__in=WEBHOOK_TOOL_NAMES,
        ).delete()
        PersistentAgentSystemSkillState.objects.filter(
            agent=agent,
            skill_key=WEBHOOKS_SYSTEM_SKILL_KEY,
        ).delete()
        if self.needs_http_request:
            result = mark_tool_enabled_without_discovery(agent, "http_request")
            if result.get("status") != "success":
                raise ValueError(f"Could not enable http_request for webhook eval: {result}")
        return agent

    def _stop_policy(self) -> dict[str, object]:
        allowed = {
            "search_tools",
            "manage_inbound_webhooks",
            "manage_outbound_webhooks",
            "send_webhook_event",
            "http_request",
            "send_chat_message",
        }
        return {
            "allowed_tool_names": sorted(allowed),
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
            "stop_on_unexpected_relevant_tool": True,
            "stop_on_tool_names_after_finish": [
                "manage_inbound_webhooks",
                "manage_outbound_webhooks",
                "send_webhook_event",
                "http_request",
                "send_chat_message",
            ],
            "max_relevant_tool_calls": 8,
        }

    def _verify(self, run_id: str, agent: PersistentAgent, inbound, calls) -> tuple[bool, str, object | None]:
        raise NotImplementedError

    def run(self, run_id: str, agent_id: str) -> None:
        agent = self._prepare_agent(agent_id)
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                self.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self.mock_config,
                eval_stop_policy=self._stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Webhook prompt was processed by the real agent harness.",
            artifacts={"message": inbound},
        )

        calls = list(
            PersistentAgentToolCall.objects.filter(
                step__eval_run_id=run_id,
                step__created_at__gte=inbound.timestamp,
            ).select_related("step").order_by("step__created_at", "step__id")
        )
        passed, summary, artifact = self._verify(run_id, agent, inbound, calls)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_behavior",
            observed_summary=summary,
            artifacts={"step": artifact.step} if artifact else {},
        )


@register_scenario
class WebhookNativeInboundProviderSetupScenario(WebhookScenarioBase):
    slug = WEBHOOK_NATIVE_INBOUND_PROVIDER_SETUP
    description = "Discovers native inbound webhooks and registers the generated endpoint with a provider API."
    prompt = (
        "Aimfox has an 'add webhook' feature. Set it up so Aimfox events trigger you. "
        "Our Aimfox API access already works, and its webhook endpoint is https://api.aimfox.test/v2/webhooks."
    )
    needs_http_request = True
    mock_config = {
        "http_request": {
            "status": "ok",
            "status_code": 201,
            "content": {"status": "ok", "webhook_id": "aimfox-eval-webhook"},
        }
    }

    def _verify(self, run_id, agent, inbound, calls):
        search_calls = [call for call in calls if call.tool_name == "search_tools"]
        create_calls = [
            call
            for call in calls
            if call.tool_name == "manage_inbound_webhooks"
            and (call.tool_params or {}).get("action") == "create"
        ]
        http_calls = [call for call in calls if call.tool_name == "http_request"]
        created_webhook = PersistentAgentInboundWebhook.objects.filter(agent=agent).first()
        expected_endpoint = build_inbound_webhook_url(created_webhook) if created_webhook else ""
        provider_registration = False
        for call in http_calls:
            params_text = json.dumps(call.tool_params or {}, sort_keys=True)
            provider_registration = provider_registration or (
                "api.aimfox.test/v2/webhooks" in str(resolved_tool_param(call, "url") or "")
                and expected_endpoint in params_text
            )
        pipedream_calls = [call for call in calls if "pipedream" in call.tool_name.casefold()]
        skill_enabled = PersistentAgentSystemSkillState.objects.filter(
            agent=agent,
            skill_key=WEBHOOKS_SYSTEM_SKILL_KEY,
            is_enabled=True,
        ).exists()
        created = created_webhook is not None
        passed = bool(search_calls and create_calls and provider_registration and skill_enabled and created and not pipedream_calls)
        summary = (
            "Agent discovered the Webhooks skill, created a native inbound endpoint, and registered it with Aimfox without Pipedream."
            if passed
            else (
                "Expected search_tools, native inbound creation, and provider registration without Pipedream; saw "
                f"{[call.tool_name for call in calls]}."
            )
        )
        artifact = create_calls[0] if create_calls else (search_calls[0] if search_calls else None)
        return passed, summary, artifact


@register_scenario
class WebhookOutboundConfigureAndSendScenario(WebhookScenarioBase):
    slug = WEBHOOK_OUTBOUND_CONFIGURE_AND_SEND
    description = "Discovers webhook tools, configures an outbound destination, and sends the requested JSON event."
    prompt = (
        "Create an outbound webhook named Deployment Status pointing to https://hooks.example.test/deploy, "
        "then send it the JSON event {\"status\": \"ready\", \"environment\": \"staging\"}."
    )
    mock_config = {
        "send_webhook_event": {
            "status": "success",
            "webhook_name": "Deployment Status",
            "response_status": 204,
            "auto_sleep_ok": True,
        }
    }

    def _verify(self, run_id, agent, inbound, calls):
        create_calls = [
            call
            for call in calls
            if call.tool_name == "manage_outbound_webhooks"
            and (call.tool_params or {}).get("action") == "create"
        ]
        send_calls = [call for call in calls if call.tool_name == "send_webhook_event"]
        configured = PersistentAgentWebhook.objects.filter(
            agent=agent,
            name="Deployment Status",
            url="https://hooks.example.test/deploy",
        ).exists()
        correct_payload = any(
            (call.tool_params or {}).get("payload")
            == {"status": "ready", "environment": "staging"}
            for call in send_calls
        )
        passed = bool(create_calls and send_calls and configured and correct_payload)
        summary = (
            "Agent configured the outbound destination and sent the requested structured event."
            if passed
            else f"Expected outbound create then send; saw {[call.tool_name for call in calls]}."
        )
        artifact = send_calls[0] if send_calls else (create_calls[0] if create_calls else None)
        return passed, summary, artifact


@register_scenario
class WebhookExplicitPipedreamAllowedScenario(WebhookScenarioBase):
    slug = WEBHOOK_EXPLICIT_PIPEDREAM_ALLOWED
    description = "Allows an explicit Pipedream webhook request without silently creating native Gobii configuration."
    prompt = (
        "I explicitly want to use a Pipedream HTTP trigger for this webhook workflow. Tell me the next setup step; "
        "do not create a Gobii webhook instead."
    )

    def _verify(self, run_id, agent, inbound, calls):
        native_mutations = [
            call
            for call in calls
            if call.tool_name in {"manage_inbound_webhooks", "manage_outbound_webhooks"}
            and (call.tool_params or {}).get("action") in {"create", "update", "rotate_secret", "delete"}
        ]
        outbound_messages = list(
            agent.agent_messages.filter(is_outbound=True, timestamp__gt=inbound.timestamp).order_by("timestamp")
        )
        mentions_pipedream = any("pipedream" in (message.body or "").casefold() for message in outbound_messages)
        no_native_rows = not agent.inbound_webhooks.exists() and not agent.webhooks.exists()
        passed = bool(not native_mutations and no_native_rows and mentions_pipedream)
        summary = (
            "Agent honored the explicit Pipedream choice without creating native webhook state."
            if passed
            else (
                "Expected Pipedream guidance and no native mutation; saw "
                f"tools={[call.tool_name for call in calls]}, messages={len(outbound_messages)}."
            )
        )
        artifact = native_mutations[0] if native_mutations else None
        return passed, summary, artifact
