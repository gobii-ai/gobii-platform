from dataclasses import dataclass, field
from typing import Any

from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemSkillState,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)


IMAGE_GENERATION_SUITE_SLUG = "image_generation"

IMAGE_GENERATION_NEW_ASSET = "image_generation_new_asset"
IMAGE_GENERATION_SOURCE_EDIT = "image_generation_source_edit"
IMAGE_GENERATION_EXACT_TEXT = "image_generation_exact_text"
IMAGE_GENERATION_MULTI_ASSET = "image_generation_multi_asset"
IMAGE_GENERATION_AVOIDS_ANALYSIS = "image_generation_avoids_analysis"

IMAGE_GENERATION_SCENARIO_SLUGS = (
    IMAGE_GENERATION_NEW_ASSET,
    IMAGE_GENERATION_SOURCE_EDIT,
    IMAGE_GENERATION_EXACT_TEXT,
    IMAGE_GENERATION_MULTI_ASSET,
    IMAGE_GENERATION_AVOIDS_ANALYSIS,
)

MESSAGE_TOOL_NAMES = ("send_chat_message", "send_email", "send_sms")
ALLOWED_SUPPORT_TOOL_NAMES = ("read_file", "search_tools", "request_human_input", *MESSAGE_TOOL_NAMES)


def _mock_image_result(file_path: str) -> dict[str, Any]:
    file_ref = f"$[{file_path}]"
    return {
        "status": "ok",
        "file": file_ref,
        "inline": f"![Generated image]({file_ref})",
        "inline_html": f"<img src='{file_ref}' alt='Generated image' />",
        "attach": file_ref,
        "eval_fixture": True,
    }


def _mock_read_file_result(file_path: str, content: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "path": file_path,
        "mime_type": "image/png",
        "content": content,
        "eval_fixture": True,
    }


@dataclass(frozen=True)
class ImageGenerationCase:
    slug: str
    description: str
    prompt: str
    mock_config: dict[str, Any]
    expected_call_count: int
    expected_aspect_ratio: str = ""
    required_prompt_groups: tuple[tuple[str, ...], ...] = ()
    required_prompt_terms_across_calls: tuple[str, ...] = ()
    required_source_images: tuple[str, ...] = ()
    required_response_refs: tuple[str, ...] = ()
    required_response_groups: tuple[tuple[str, ...], ...] = ()
    allowed_extra_tool_names: tuple[str, ...] = ()
    forbid_create_image: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def eval_stop_policy(self) -> dict[str, Any]:
        allowed = {"create_image", *ALLOWED_SUPPORT_TOOL_NAMES, *self.allowed_extra_tool_names}
        return {
            "allowed_tool_names": sorted(allowed),
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
            "stop_on_tool_names": ["create_image"] if self.forbid_create_image else [],
            "stop_on_unexpected_relevant_tool": True,
            "max_relevant_tool_calls": max(self.expected_call_count + 3, 4),
            "stop_on_human_input_request": False,
        }


IMAGE_GENERATION_CASES = (
    ImageGenerationCase(
        slug=IMAGE_GENERATION_NEW_ASSET,
        description="Generate one well-specified raster hero and reuse the returned filespace placeholder.",
        prompt=(
            "Create a 16:9 landing-page hero image for a neighborhood coffee subscription. Show a ceramic mug "
            "and fresh beans on a warm kitchen counter in natural morning light, with room for headline copy. "
            "Save it as a new image and show me the result."
        ),
        mock_config={"create_image": _mock_image_result("/exports/eval-coffee-hero.png")},
        expected_call_count=1,
        expected_aspect_ratio="16:9",
        required_prompt_groups=(
            ("landing-page", "landing page", "hero"),
            ("ceramic mug", "coffee mug"),
            ("headline", "negative space", "copy"),
        ),
        required_response_refs=("$[/exports/eval-coffee-hero.png]",),
        tags=("generation", "placeholder"),
    ),
    ImageGenerationCase(
        slug=IMAGE_GENERATION_SOURCE_EDIT,
        description="Use a source image, preserve invariants, and save the edit non-destructively.",
        prompt=(
            "Using `$[/Inbox/product.png]`, change only the background to a deep navy studio backdrop. Keep the "
            "bottle, label text, proportions, lighting, and camera angle unchanged. Save the edited image separately."
        ),
        mock_config={
            "read_file": _mock_read_file_result(
                "/Inbox/product.png",
                "The product PNG fixture exists and is available at /Inbox/product.png.",
            ),
            "create_image": _mock_image_result("/exports/eval-product-edit.png"),
        },
        expected_call_count=1,
        required_prompt_groups=(
            ("change only",),
            ("preserve", "keep"),
            ("label text", "label"),
            ("deep navy", "navy"),
        ),
        required_source_images=("/Inbox/product.png",),
        required_response_refs=("$[/exports/eval-product-edit.png]",),
        tags=("editing", "source_images", "invariants"),
    ),
    ImageGenerationCase(
        slug=IMAGE_GENERATION_EXACT_TEXT,
        description="Preserve exact marketing copy and prohibit additional in-image text.",
        prompt=(
            "Create a 4:5 running-shoe campaign poster. The only text should be the exact tagline "
            "\"Built for the Long Run.\" Do not add any other copy."
        ),
        mock_config={"create_image": _mock_image_result("/exports/eval-running-poster.png")},
        expected_call_count=1,
        expected_aspect_ratio="4:5",
        required_prompt_groups=(
            ("Built for the Long Run.",),
            ("verbatim", "exact"),
            ("no extra text", "no other text", "no other copy", "only text"),
        ),
        required_response_refs=("$[/exports/eval-running-poster.png]",),
        tags=("exact_text", "marketing"),
    ),
    ImageGenerationCase(
        slug=IMAGE_GENERATION_MULTI_ASSET,
        description="Create three distinct assets with separate prompts and non-destructive output paths.",
        prompt=(
            "Create three separate 1:1 campaign images for a coffee subscription: a bright morning kitchen scene, "
            "a focused afternoon desk scene, and a cozy evening reading scene. Save each as its own new file and "
            "show me all three results."
        ),
        mock_config={
            "create_image": {
                "rules": [
                    {
                        "param_contains": {"prompt": "morning"},
                        "result": _mock_image_result("/exports/eval-coffee-morning.png"),
                    },
                    {
                        "param_contains": {"prompt": "afternoon"},
                        "result": _mock_image_result("/exports/eval-coffee-afternoon.png"),
                    },
                    {
                        "param_contains": {"prompt": "evening"},
                        "result": _mock_image_result("/exports/eval-coffee-evening.png"),
                    },
                ],
                "default": _mock_image_result("/exports/eval-coffee-unmatched.png"),
            }
        },
        expected_call_count=3,
        expected_aspect_ratio="1:1",
        required_prompt_terms_across_calls=("morning", "afternoon", "evening"),
        required_response_refs=(
            "$[/exports/eval-coffee-morning.png]",
            "$[/exports/eval-coffee-afternoon.png]",
            "$[/exports/eval-coffee-evening.png]",
        ),
        tags=("multi_asset", "non_destructive"),
    ),
    ImageGenerationCase(
        slug=IMAGE_GENERATION_AVOIDS_ANALYSIS,
        description="Avoid using image generation for OCR or extracting facts from an existing image.",
        prompt=(
            "Please read the receipt in `$[/Inbox/receipt.png]` and tell me the merchant, purchase date, and total."
        ),
        mock_config={
            "sqlite_batch": {
                "status": "ok",
                "results": [
                    {
                        "result": [
                            {
                                "path": "/Inbox/receipt.png",
                                "mime_type": "image/png",
                                "filename": "receipt.png",
                            }
                        ],
                        "message": "Query returned 1 filespace image.",
                    }
                ],
            },
            "read_file": _mock_read_file_result(
                "/Inbox/receipt.png",
                "Synthetic receipt OCR: Merchant: Northstar Market; Purchase date: 2026-07-08; Total: $42.17.",
            )
        },
        expected_call_count=0,
        required_response_groups=(
            ("Northstar Market",),
            ("2026-07-08", "July 8, 2026", "Jul 8, 2026"),
            ("$42.17", "42.17"),
        ),
        allowed_extra_tool_names=("sqlite_batch",),
        forbid_create_image=True,
        tags=("negative_routing", "ocr"),
    ),
)


def _tool_calls_for_run(run_id: str, *, after=None) -> list[PersistentAgentToolCall]:
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id, tool_name="create_image")
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def _normalize_source_path(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("$[") and text.endswith("]"):
        return text[2:-1].strip()
    return text


def _candidate_response_bodies(run_id: str, agent_id: str, inbound) -> list[tuple[str, object]]:
    bodies: list[tuple[str, object]] = []
    for message in (
        PersistentAgentMessage.objects
        .filter(owner_agent_id=agent_id, is_outbound=True, timestamp__gt=inbound.timestamp)
        .order_by("seq")
    ):
        bodies.append((message.body or "", message))

    for call in (
        PersistentAgentToolCall.objects
        .filter(step__eval_run_id=run_id, step__created_at__gte=inbound.timestamp, tool_name__in=MESSAGE_TOOL_NAMES)
        .select_related("step")
        .order_by("step__created_at", "step__id")
    ):
        params = call.tool_params or {}
        body = str(params.get("body") or params.get("message") or "")
        if body:
            bodies.append((body, call))

    for request in (
        PersistentAgentHumanInputRequest.objects
        .filter(agent_id=agent_id, originating_step__eval_run_id=run_id, created_at__gt=inbound.timestamp)
        .order_by("created_at", "id")
    ):
        bodies.append((request.question or "", request))
    return bodies


class ImageGenerationScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "image_generation"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("image_generation", "system_skill", "real_harness", "micro")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_tool_calls", assertion_type="tool_call"),
        ScenarioTask(name="verify_prompt_contract", assertion_type="exact_match"),
        ScenarioTask(name="verify_response", assertion_type="exact_match"),
    ]
    case: ImageGenerationCase | None = None

    def _case(self) -> ImageGenerationCase:
        if self.case is None:
            raise ValueError(f"{type(self).__name__}.case must be set.")
        return self.case

    @staticmethod
    def _seed_prior_processing_run(agent_id: str) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return
        step = PersistentAgentStep.objects.create(agent_id=agent_id, description="Process events")
        PersistentAgentSystemStep.objects.create(step=step, code=PersistentAgentSystemStep.Code.PROCESS_EVENTS)

    def _prepare_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        agent = PersistentAgent.objects.get(id=agent_id)
        result = mark_tool_enabled_without_discovery(agent, "create_image")
        if result.get("status") != "success":
            raise ValueError(f"Could not enable eval create_image tool: {result}")
        PersistentAgentEnabledTool.objects.filter(agent=agent, tool_full_name="create_image").update(
            tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
            tool_name="create_image",
        )
        if not PersistentAgentSystemSkillState.objects.filter(
            agent=agent,
            skill_key="image_generation",
            is_enabled=True,
        ).exists():
            raise ValueError("Image generation system skill was not enabled with create_image.")

    def _record_tool_calls(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_tool_calls")
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        complete_calls = [call for call in calls if call.status == "complete"]
        if len(complete_calls) != case.expected_call_count:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_tool_calls",
                observed_summary=(
                    f"Expected {case.expected_call_count} completed create_image call(s); "
                    f"saw {len(complete_calls)} completed and {len(calls)} total."
                ),
                artifacts={"step": calls[0].step} if calls else {},
            )
            return
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_tool_calls",
            observed_summary=f"Observed the expected {case.expected_call_count} create_image call(s).",
            artifacts={"step": calls[0].step} if calls else {},
        )

    def _prompt_contract_errors(self, calls: list[PersistentAgentToolCall]) -> list[str]:
        case = self._case()
        if case.forbid_create_image:
            return [] if not calls else ["create_image was used for an analysis/OCR request"]
        if len(calls) != case.expected_call_count:
            return [f"expected {case.expected_call_count} calls, saw {len(calls)}"]

        params = [call.tool_params or {} for call in calls]
        prompts = [str(item.get("prompt") or "") for item in params]
        lowered_prompts = [prompt.lower() for prompt in prompts]
        errors: list[str] = []

        for alternatives in case.required_prompt_groups:
            if not any(any(term.lower() in prompt for term in alternatives) for prompt in lowered_prompts):
                errors.append(f"missing prompt term group {alternatives}")
        for term in case.required_prompt_terms_across_calls:
            if not any(term.lower() in prompt for prompt in lowered_prompts):
                errors.append(f"missing distinct asset term {term!r}")

        if case.expected_call_count > 1 and len(set(lowered_prompts)) != len(lowered_prompts):
            errors.append("distinct assets reused an identical prompt")

        paths = [str(item.get("file_path") or "") for item in params]
        if any(not path.startswith("/exports/") for path in paths):
            errors.append(f"output paths were not all under /exports/: {paths}")
        if len(set(paths)) != len(paths):
            errors.append(f"output paths were not distinct: {paths}")
        if any(item.get("overwrite") is True for item in params):
            errors.append("a call requested destructive overwrite")

        if case.expected_aspect_ratio and any(
            str(item.get("aspect_ratio") or "") != case.expected_aspect_ratio for item in params
        ):
            errors.append(f"expected aspect ratio {case.expected_aspect_ratio}")

        if case.required_source_images:
            actual_sources = {
                _normalize_source_path(source)
                for item in params
                for source in (item.get("source_images") or [])
            }
            missing_sources = set(case.required_source_images) - actual_sources
            if missing_sources:
                errors.append(f"missing source image paths {sorted(missing_sources)}")
            if any(path in actual_sources for path in paths):
                errors.append("an edit overwrote its source path")
        elif any(item.get("source_images") for item in params):
            errors.append("source_images were supplied for a from-scratch generation request")
        return errors

    def _record_prompt_contract(self, run_id: str, inbound) -> None:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_prompt_contract")
        calls = _tool_calls_for_run(run_id, after=inbound.timestamp)
        errors = self._prompt_contract_errors(calls)
        if errors:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_prompt_contract",
                observed_summary="; ".join(errors),
                artifacts={"step": calls[0].step} if calls else {},
            )
            return
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_prompt_contract",
            observed_summary="create_image arguments followed the image-generation skill contract.",
            artifacts={"step": calls[0].step} if calls else {},
        )

    def _record_response(self, run_id: str, agent_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_response")
        responses = _candidate_response_bodies(run_id, agent_id, inbound)
        matching = [
            (body, artifact)
            for body, artifact in responses
            if all(reference in body for reference in case.required_response_refs)
            and all(
                any(term.lower() in body.lower() for term in alternatives)
                for alternatives in case.required_response_groups
            )
        ]
        if not matching:
            latest_body = responses[-1][0] if responses else ""
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_response",
                observed_summary=(
                    f"No response contained expected placeholders {case.required_response_refs} and terms "
                    f"{case.required_response_groups}; "
                    f"latest response={latest_body[:800]!r}."
                ),
                artifacts={"response_artifact": responses[-1][1]} if responses else {},
            )
            return
        body, artifact = matching[-1]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_response",
            observed_summary="Agent returned the expected image placeholders or an appropriate non-generation response.",
            artifacts={"response_artifact": artifact, "response_preview": body[:800]},
        )

    def run(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        self._prepare_agent(agent_id)
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=180):
            inbound = self.inject_message(
                agent_id,
                case.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=case.mock_config,
                eval_stop_policy=case.eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )
        self._record_tool_calls(run_id, inbound)
        self._record_prompt_contract(run_id, inbound)
        self._record_response(run_id, agent_id, inbound)


for image_generation_case in IMAGE_GENERATION_CASES:
    scenario_type = type(
        "".join(part.title() for part in image_generation_case.slug.split("_")) + "Scenario",
        (ImageGenerationScenario,),
        {
            "slug": image_generation_case.slug,
            "description": image_generation_case.description,
            "tags": ImageGenerationScenario.tags + image_generation_case.tags,
            "case": image_generation_case,
        },
    )
    ScenarioRegistry.register(scenario_type())
