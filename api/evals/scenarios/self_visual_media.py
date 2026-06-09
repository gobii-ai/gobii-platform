import json
import uuid
from typing import Any, Iterable

from django.utils import timezone

from api.agent.core.prompt_context import build_prompt_context_preview
from api.agent.files.attachment_helpers import AttachmentResolutionError, normalize_attachment_paths
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.models import (
    CommsAllowlistEntry,
    CommsChannel,
    EvalRun,
    EvalRunTask,
    ImageGenerationLLMTier,
    ImageGenerationModelEndpoint,
    ImageGenerationTierEndpoint,
    LLMProvider,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    SmsContactPurpose,
    VideoGenerationLLMTier,
    VideoGenerationModelEndpoint,
    VideoGenerationTierEndpoint,
)


SELF_VISUAL_MEDIA_SUITE_SLUG = "self_visual_media"
SELF_IMAGE_SMS_ATTACHMENT = "self_image_sms_attachment"
SELF_VIDEO_SMS_ATTACHMENT = "self_video_sms_attachment"
SELF_VISUAL_DESCRIPTION_NOT_IN_ORDINARY_PROMPT = "self_visual_description_not_in_ordinary_prompt"

SELF_VISUAL_MEDIA_SCENARIO_SLUGS = (
    SELF_IMAGE_SMS_ATTACHMENT,
    SELF_VIDEO_SMS_ATTACHMENT,
    SELF_VISUAL_DESCRIPTION_NOT_IN_ORDINARY_PROMPT,
)

IMAGE_VISUAL_DESCRIPTION = (
    "A cinematic Gobii with iridescent teal hair, brass round glasses, "
    "and a sunflower-yellow raincoat."
)
VIDEO_VISUAL_DESCRIPTION = (
    "A friendly Gobii with midnight-blue curls, copper aviator glasses, "
    "and a moss linen jacket."
)
NEGATIVE_VISUAL_DESCRIPTION = (
    "A rare prompt-efficiency Gobii with ruby glasses, silver braids, and a cobalt scarf."
)


class SelfVisualMediaScenarioBase(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "self_visual_media"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "media_generation"
    tags = ("agent_behavior", "self_visual_media", "media_generation")
    supports_simulation = True

    @staticmethod
    def _is_simulated(run_id: str) -> bool:
        run = EvalRun.objects.select_related("suite_run").get(id=run_id)
        suite_run = run.suite_run
        return bool(suite_run and (suite_run.launch_config or {}).get("mode") == "simulated")

    @staticmethod
    def _unique_eval_number(agent_id: str, *, offset: int = 0) -> str:
        suffix = (uuid.UUID(str(agent_id)).int + offset) % 10_000_000
        return f"+1555{suffix:07d}"

    @staticmethod
    def _message_text(messages: Iterable[dict[str, Any]]) -> str:
        return "\n".join(
            str(message.get("content") or "")
            for message in messages
            if isinstance(message, dict)
        )

    @staticmethod
    def _tool_params(call: PersistentAgentToolCall | None) -> dict[str, Any]:
        if call is None:
            return {}
        params = call.tool_params or {}
        return params if isinstance(params, dict) else {}

    @staticmethod
    def _tool_calls(
        run_id: str,
        *,
        tool_name: str | None = None,
        after=None,
    ) -> list[PersistentAgentToolCall]:
        queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
        if tool_name:
            queryset = queryset.filter(tool_name=tool_name)
        if after is not None:
            queryset = queryset.filter(step__created_at__gte=after)
        return list(queryset.select_related("step").order_by("step__created_at", "step__id"))

    def _seed_prior_processing_run(self, agent_id: str) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return
        step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Prior eval process run",
        )
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    def _prepare_agent(self, agent_id: str, *, visual_description: str) -> tuple[PersistentAgent, str]:
        target_number = self._unique_eval_number(agent_id, offset=23)
        from_number = self._unique_eval_number(agent_id, offset=41)
        short_id = str(agent_id).split("-", 1)[0]
        PersistentAgent.objects.filter(id=agent_id).update(
            name=f"Self Visual Media Eval {short_id}",
            organization=None,
            visual_description=visual_description,
            charter="You are a test agent. Do not expose private/internal setup details.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
            sms_disabled=False,
        )
        agent = PersistentAgent.objects.get(id=agent_id)
        self._seed_prior_processing_run(agent_id)

        endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.SMS,
            address=from_number,
            defaults={"owner_agent": agent, "is_primary": True},
        )
        if endpoint.owner_agent_id != agent.id or not endpoint.is_primary:
            endpoint.owner_agent = agent
            endpoint.is_primary = True
            endpoint.save(update_fields=["owner_agent", "is_primary"])

        CommsAllowlistEntry.objects.update_or_create(
            agent=agent,
            channel=CommsChannel.SMS,
            address=target_number,
            defaults={
                "is_active": True,
                "verified": True,
                "allow_inbound": True,
                "allow_outbound": True,
                "sms_contact_purpose": SmsContactPurpose.OTHER_OPERATIONAL,
                "sms_contact_purpose_details": "Eval-only fake SMS recipient.",
                "sms_contact_permission_attested": True,
                "sms_contact_permission_attested_at": timezone.now(),
            },
        )
        return agent, target_number

    def _seed_image_generation_tier(self) -> None:
        provider, _ = LLMProvider.objects.update_or_create(
            key="eval-self-visual-media",
            defaults={"display_name": "Eval Self Visual Media", "enabled": True},
        )
        endpoint, _ = ImageGenerationModelEndpoint.objects.update_or_create(
            key="eval-self-visual-image",
            defaults={
                "provider": provider,
                "enabled": True,
                "litellm_model": "eval/self-visual-image",
            },
        )
        tier, _ = ImageGenerationLLMTier.objects.update_or_create(
            use_case=ImageGenerationLLMTier.UseCase.CREATE_IMAGE,
            order=997,
            defaults={"description": "Eval self visual image tier"},
        )
        ImageGenerationTierEndpoint.objects.update_or_create(
            tier=tier,
            endpoint=endpoint,
            defaults={"weight": 1.0},
        )

    def _seed_video_generation_tier(self) -> None:
        provider, _ = LLMProvider.objects.update_or_create(
            key="eval-self-visual-media",
            defaults={"display_name": "Eval Self Visual Media", "enabled": True},
        )
        endpoint, _ = VideoGenerationModelEndpoint.objects.update_or_create(
            key="eval-self-visual-video",
            defaults={
                "provider": provider,
                "enabled": True,
                "litellm_model": "eval/self-visual-video",
            },
        )
        tier, _ = VideoGenerationLLMTier.objects.update_or_create(
            use_case=VideoGenerationLLMTier.UseCase.CREATE_VIDEO,
            order=997,
            defaults={"description": "Eval self visual video tier"},
        )
        VideoGenerationTierEndpoint.objects.update_or_create(
            tier=tier,
            endpoint=endpoint,
            defaults={"weight": 1.0},
        )

    def _record_simulated_tool_call(
        self,
        run_id: str,
        agent_id: str,
        *,
        tool_name: str,
        tool_params: dict[str, Any],
        result: dict[str, Any],
    ) -> PersistentAgentToolCall:
        step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            eval_run_id=run_id,
            description=f"Simulated eval tool call: {tool_name}",
        )
        return PersistentAgentToolCall.objects.create(
            step=step,
            tool_name=tool_name,
            tool_params=tool_params,
            result=json.dumps(result),
        )

    def _record_visual_identity_retrieved(self, run_id: str, after, *, required_term: str) -> None:
        task_name = "verify_visual_identity_retrieved"
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = self._tool_calls(run_id, tool_name="get_self_visual_identity", after=after)
        if calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary="Agent retrieved visual identity before media generation.",
                artifacts={"step": calls[0].step, "tool_names": [call.tool_name for call in calls]},
            )
            return
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            expected_summary=f"Expected get_self_visual_identity before media generation; visual term={required_term}.",
            observed_summary="No get_self_visual_identity tool call was recorded.",
        )

    def _record_media_prompt_contains_identity(
        self,
        run_id: str,
        after,
        *,
        media_tool_name: str,
        required_terms: tuple[str, ...],
    ) -> None:
        task_name = "verify_media_prompt_contains_identity"
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = self._tool_calls(run_id, tool_name=media_tool_name, after=after)
        if not calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                expected_summary=f"Expected a {media_tool_name} call containing the visual identity.",
                observed_summary=f"No {media_tool_name} tool call was recorded.",
            )
            return

        prompt = str(self._tool_params(calls[0]).get("prompt") or "")
        lowered_prompt = prompt.lower()
        missing_terms = [term for term in required_terms if term.lower() not in lowered_prompt]
        if not missing_terms:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary=f"{media_tool_name} prompt included the retrieved visual identity.",
                artifacts={
                    "step": calls[0].step,
                    "prompt_length": len(prompt),
                    "required_terms_present": list(required_terms),
                },
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            expected_summary=f"Expected {media_tool_name}.prompt to include visual identity terms.",
            observed_summary=f"Missing visual identity terms in media prompt: {', '.join(missing_terms)}.",
            artifacts={"step": calls[0].step, "prompt_length": len(prompt)},
        )

    def _record_sms_attachment(self, run_id: str, after, *, target_number: str, attach: str) -> None:
        task_name = "verify_sms_attachment"
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name=task_name)
        calls = self._tool_calls(run_id, tool_name="send_sms", after=after)
        if not calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                expected_summary="Expected send_sms with the generated media attachment.",
                observed_summary="No send_sms tool call was recorded.",
            )
            return

        params = self._tool_params(calls[-1])
        attachments = params.get("attachments") or []
        if isinstance(attachments, str):
            attachments = [attachments]
        try:
            normalized_attachments = normalize_attachment_paths(attachments)
            expected_paths = normalize_attachment_paths([attach])
        except AttachmentResolutionError:
            normalized_attachments = []
            expected_paths = []
        attachment_ok = bool(expected_paths and expected_paths[0] in normalized_attachments)
        target_ok = str(params.get("to_number") or "").strip() == target_number
        if attachment_ok and target_ok:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=task_name,
                observed_summary="send_sms included the generated media placeholder in attachments.",
                artifacts={
                    "step": calls[-1].step,
                    "attachment_count": len(attachments),
                    "normalized_attachment_count": len(normalized_attachments),
                    "target_number_matches": True,
                },
            )
            return

        failures = []
        if not attachment_ok:
            failures.append("missing generated attachment placeholder")
        if not target_ok:
            failures.append("to_number did not match fake eval recipient")
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=task_name,
            expected_summary="Expected send_sms.to_number and send_sms.attachments to match the generated media.",
            observed_summary=", ".join(failures),
            artifacts={"step": calls[-1].step, "attachment_count": len(attachments)},
        )


class SelfVisualMediaAttachmentScenario(SelfVisualMediaScenarioBase):
    media_tool_name = ""
    media_path = ""
    attach = ""
    visual_description = ""
    required_terms: tuple[str, ...] = ()
    prompt_template = ""
    mock_media_result: dict[str, Any] = {}

    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_visual_identity_retrieved", assertion_type="tool_call"),
        ScenarioTask(name="verify_media_prompt_contains_identity", assertion_type="tool_call"),
        ScenarioTask(name="verify_sms_attachment", assertion_type="tool_call"),
    ]

    def _mock_config(self) -> dict[str, dict[str, Any]]:
        return {
            self.media_tool_name: {"default": self.mock_media_result},
            "send_sms": {
                "default": {
                    "status": "ok",
                    "message": "SMS queued for eval recipient.",
                    "message_id": f"eval-{self.slug}-sms",
                    "auto_sleep_ok": True,
                }
            },
        }

    def _eval_stop_policy(self) -> dict[str, Any]:
        return {
            "ignored_tool_names": ["send_chat_message", "sleep_until_next_trigger", "update_plan"],
            "allowed_tool_names": [
                "search_tools",
                "get_self_visual_identity",
                self.media_tool_name,
                "send_sms",
            ],
            "stop_on_unexpected_relevant_tool": True,
            "stop_when_all_seen": [{"tool_name": "send_sms", "after_finish": True}],
            "max_relevant_tool_calls": 8,
        }

    def _seed_media_generation_tier(self) -> None:
        raise NotImplementedError

    def _record_simulated_flow(self, run_id: str, agent_id: str, *, target_number: str, prompt: str) -> None:
        self._record_simulated_tool_call(
            run_id,
            agent_id,
            tool_name="get_self_visual_identity",
            tool_params={"purpose": "generate and send self visual media"},
            result={
                "status": "ok",
                "agent_name": f"Self Visual Media Eval {str(agent_id).split('-', 1)[0]}",
                "visual_description": self.visual_description,
            },
        )
        self._record_simulated_tool_call(
            run_id,
            agent_id,
            tool_name=self.media_tool_name,
            tool_params={
                "prompt": f"{prompt}\n\nVisual description: {self.visual_description}",
                "file_path": self.media_path,
            },
            result=self.mock_media_result,
        )
        self._record_simulated_tool_call(
            run_id,
            agent_id,
            tool_name="send_sms",
            tool_params={
                "to_number": target_number,
                "body": "Here is the generated media.",
                "attachments": [self.attach],
                "will_continue_work": False,
            },
            result={
                "status": "ok",
                "message": "SMS queued for eval recipient.",
                "message_id": f"eval-{self.slug}-sms",
                "auto_sleep_ok": True,
            },
        )

    def run(self, run_id: str, agent_id: str) -> None:
        self._seed_media_generation_tier()
        _agent, target_number = self._prepare_agent(
            agent_id,
            visual_description=self.visual_description,
        )
        prompt = self.prompt_template.format(target_number=target_number)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        if self._is_simulated(run_id):
            inbound = self.inject_message(
                agent_id,
                prompt,
                trigger_processing=False,
                eval_run_id=run_id,
            )
            self._record_simulated_flow(run_id, agent_id, target_number=target_number, prompt=prompt)
        else:
            with self.wait_for_agent_idle(agent_id, timeout=180):
                inbound = self.inject_message(
                    agent_id,
                    prompt,
                    trigger_processing=True,
                    eval_run_id=run_id,
                    mock_config=self._mock_config(),
                    eval_stop_policy=self._eval_stop_policy(),
                )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self._record_visual_identity_retrieved(
            run_id,
            inbound.timestamp,
            required_term=self.required_terms[0],
        )
        self._record_media_prompt_contains_identity(
            run_id,
            inbound.timestamp,
            media_tool_name=self.media_tool_name,
            required_terms=self.required_terms,
        )
        self._record_sms_attachment(
            run_id,
            inbound.timestamp,
            target_number=target_number,
            attach=self.attach,
        )


@register_scenario
class SelfImageSmsAttachmentScenario(SelfVisualMediaAttachmentScenario):
    slug = SELF_IMAGE_SMS_ATTACHMENT
    description = (
        "A selfie SMS request should dynamically retrieve this Gobii's visual identity, "
        "include it in the create_image prompt, and send the generated image placeholder as an SMS attachment."
    )
    media_tool_name = "create_image"
    media_path = "/exports/eval-selfie.png"
    attach = "$[/exports/eval-selfie.png]"
    visual_description = IMAGE_VISUAL_DESCRIPTION
    required_terms = ("iridescent teal hair", "brass round glasses", "sunflower-yellow raincoat")
    prompt_template = (
        "Text me a selfie of yourself at {target_number}. Generate the image first, "
        "then send the generated image as an SMS attachment. Keep the SMS body short."
    )
    mock_media_result = {
        "status": "ok",
        "message": "Image generated.",
        "file": attach,
        "attach": attach,
        "mime_type": "image/png",
        "self_visual_identity_included": True,
    }

    def _seed_media_generation_tier(self) -> None:
        self._seed_image_generation_tier()


@register_scenario
class SelfVideoSmsAttachmentScenario(SelfVisualMediaAttachmentScenario):
    slug = SELF_VIDEO_SMS_ATTACHMENT
    description = (
        "A self-video SMS request should dynamically retrieve this Gobii's visual identity, "
        "include it in the create_video prompt, and send the generated video placeholder as an SMS attachment."
    )
    media_tool_name = "create_video"
    media_path = "/exports/eval-self-video.mp4"
    attach = "$[/exports/eval-self-video.mp4]"
    visual_description = VIDEO_VISUAL_DESCRIPTION
    required_terms = ("midnight-blue curls", "copper aviator glasses", "moss linen jacket")
    prompt_template = (
        "Text me a short video of yourself waving at {target_number}. Generate the video first, "
        "then send the generated video as an SMS attachment. Keep the SMS body short."
    )
    mock_media_result = {
        "status": "ok",
        "message": "Video generated.",
        "file": attach,
        "attach": attach,
        "mime_type": "video/mp4",
        "self_visual_identity_included": True,
    }

    def _seed_media_generation_tier(self) -> None:
        self._seed_video_generation_tier()


@register_scenario
class SelfVisualDescriptionNotInOrdinaryPromptScenario(SelfVisualMediaScenarioBase):
    slug = SELF_VISUAL_DESCRIPTION_NOT_IN_ORDINARY_PROMPT
    description = (
        "Ordinary non-visual tasks should not carry the Gobii visual description in the normal prompt context."
    )
    tasks = [
        ScenarioTask(name="inject_ordinary_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_visual_identity_absent", assertion_type="exact_match"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        agent, _target_number = self._prepare_agent(
            agent_id,
            visual_description=NEGATIVE_VISUAL_DESCRIPTION,
        )
        ordinary_prompt = "Please summarize tomorrow's calendar in one concise sentence."

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_ordinary_prompt")
        inbound = self.inject_message(
            agent_id,
            ordinary_prompt,
            trigger_processing=False,
            eval_run_id=run_id,
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_ordinary_prompt",
            observed_summary="Ordinary non-visual prompt injected without model processing.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_visual_identity_absent")
        agent.refresh_from_db()
        messages, token_count, _metadata = build_prompt_context_preview(agent, is_first_run=False)
        prompt_text = self._message_text(messages)
        forbidden_terms = ("ruby glasses", "silver braids", "cobalt scarf")
        present_terms = [term for term in forbidden_terms if term in prompt_text]
        if present_terms:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_visual_identity_absent",
                expected_summary="Expected normal prompt context to omit visual identity terms.",
                observed_summary=f"Prompt context included visual terms: {', '.join(present_terms)}.",
                artifacts={"token_count": token_count},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_visual_identity_absent",
            observed_summary="Normal prompt context omitted the Gobii visual description.",
            artifacts={"token_count": token_count, "checked_terms": list(forbidden_terms)},
        )
