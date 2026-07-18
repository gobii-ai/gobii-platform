import json
import re
from dataclasses import dataclass
from typing import Any

from api.agent.tools.eval_synthetic_tools import EVAL_SYNTHETIC_TOOL_SERVER
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import EvalRunTask, PersistentAgent, PersistentAgentEnabledTool, PersistentAgentToolCall


REDDIT_CONTENT_QUALITY_SUITE_SLUG = "reddit_content_quality"
REDDIT_REPLY_TOOL = "mcp_reddit_reply-to-item"
REDDIT_CREATE_POST_TOOL = "mcp_reddit_create-post"
REDDIT_WRITE_TOOLS = {REDDIT_REPLY_TOOL, REDDIT_CREATE_POST_TOOL}


@dataclass(frozen=True)
class RedditContentQualityCase:
    slug: str
    action: str
    brief: str
    source_context: str
    subreddit: str
    target_url: str = ""
    judge_focus: str = ""

    @property
    def expected_tool(self) -> str:
        if self.action == "comment":
            return REDDIT_REPLY_TOOL
        if self.action == "post":
            return REDDIT_CREATE_POST_TOOL
        raise ValueError(f"Unsupported Reddit content action: {self.action}")


REDDIT_CONTENT_QUALITY_CASES = (
    RedditContentQualityCase(
        slug="reddit_quality_comment_llm_cost_controls",
        action="comment",
        brief="reply to a startup operator asking when LLM costs require production optimization",
        subreddit="startups",
        target_url=(
            "https://www.reddit.com/r/startups/comments/eval_llm_costs/"
            "when_did_llm_costs_become_a_serious_problem/"
        ),
        source_context=(
            "Post title: When did LLM costs become a serious problem for your startup?\n"
            "Post body: Our AI features are moving beyond MVP traffic. Startup credits still cover much of the bill, "
            "but usage is rising. When should optimization become production engineering, and what should we measure "
            "first?\n"
            "Available technical context: Tag calls with a workflow identifier and measure cost and latency per "
            "workflow. That makes it possible to find expensive fan-out before choosing caching, model routing, or "
            "deterministic replacements.\n"
            "Sender context: No company deployment history, spend, savings, or measured results were provided."
        ),
        judge_focus=(
            "The reply should contribute a concrete, technically sound next step without pretending the author has "
            "run this at a company or measured an outcome."
        ),
    ),
    RedditContentQualityCase(
        slug="reddit_quality_comment_feature_ablation",
        action="comment",
        brief="respond to a technical discussion about a noisy predictive feature",
        subreddit="MachineLearning",
        target_url=(
            "https://www.reddit.com/r/MachineLearning/comments/eval_queue_feature/"
            "using_queue_depth_as_a_delivery_eta_feature/"
        ),
        source_context=(
            "Post title: Using queue depth as a delivery ETA feature\n"
            "Post body: Adding queue depth produced a small offline lift, but the result varies sharply by region. "
            "I am deciding whether to keep the feature and would value another way to test it.\n"
            "Available technical context: Queue depth may proxy for staffing and time of day. A useful check is a "
            "region-stratified ablation against a time-of-day-only baseline, followed by evaluation on a later time "
            "window.\n"
            "Sender context: No additional experiment results or firsthand project experience were provided."
        ),
        judge_focus=(
            "The reply should engage the actual experimental uncertainty. A concise suggestion or question is enough; "
            "generic approval and a pile of loosely related advice are not."
        ),
    ),
    RedditContentQualityCase(
        slug="reddit_quality_post_open_source_feedback",
        action="post",
        brief="create a factual open-source project feedback post",
        subreddit="selfhosted",
        source_context=(
            "Project: LedgerLeaf, an open-source command-line tool maintained by the poster.\n"
            "Current state: alpha; imports CSV exports into a local SQLite database; supports rule-based category "
            "mapping; repository is https://code.example.test/ledgerleaf.\n"
            "Purpose of the post: ask self-hosters for feedback on the schema and migration documentation before the "
            "first beta.\n"
            "Unknown: user count, performance benchmarks, adoption, testimonials, production reliability, and release "
            "date."
        ),
        judge_focus=(
            "The post should make the maintainer relationship and alpha status clear, explain what exists, and ask a "
            "specific community-relevant question without manufacturing traction or turning into a launch ad."
        ),
    ),
)

REDDIT_CONTENT_QUALITY_SCENARIO_SLUGS = tuple(case.slug for case in REDDIT_CONTENT_QUALITY_CASES)


class RedditContentQualityScenario(EvalScenario, ScenarioExecutionTools):
    tier = "extended"
    category = "reddit_content_quality"
    expected_runtime = "medium"
    cost_class = "high"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = (
        "reddit",
        "content_quality",
        "response_quality",
        "human_output",
        "llm_judge",
    )
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_reddit_write", assertion_type="manual"),
        ScenarioTask(name="verify_content_basics", assertion_type="exact_match"),
        ScenarioTask(name="judge_reddit_content_quality", assertion_type="llm_judge"),
    ]
    case: RedditContentQualityCase | None = None

    def run(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        self._enable_reddit_tool(agent_id, case.expected_tool)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                self._prompt(case),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._mock_config(case),
                eval_stop_policy={
                    "stop_on_tool_names_after_execution": [case.expected_tool],
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": ["update_plan", case.expected_tool],
                    "ignored_tool_names": ["update_plan"],
                    "max_relevant_tool_calls": 3,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            expected_summary=f"Agent should publish one Reddit {case.action} through {case.expected_tool}.",
            artifacts={"message": inbound},
        )

        write_call = self._record_reddit_write(run_id, case, after=inbound.timestamp)
        if write_call is None:
            for task_name in ("verify_content_basics", "judge_reddit_content_quality"):
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.FAILED,
                    task_name=task_name,
                    observed_summary="No expected Reddit write call to inspect.",
                )
            return

        params = self._tool_params(write_call)
        self._record_content_basics(run_id, case, write_call, params)
        self._record_quality_judgment(run_id, case, write_call, params)

    def _case(self) -> RedditContentQualityCase:
        if self.case is None:
            raise ValueError("RedditContentQualityScenario.case must be set.")
        return self.case

    @staticmethod
    def _enable_reddit_tool(agent_id: str, tool_name: str) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        mark_tool_enabled_without_discovery(agent, tool_name)
        PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            tool_full_name=tool_name,
        ).update(
            tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
            tool_name=tool_name,
        )

    @staticmethod
    def _prompt(case: RedditContentQualityCase) -> str:
        if case.action == "comment":
            return (
                "Reply to this Reddit discussion using the supplied context. "
                "Do not browse or ask follow-up questions.\n\n"
                f"Reddit URL: {case.target_url}\n"
                f"Subreddit: r/{case.subreddit}\n\n"
                f"{case.source_context}\n\nPost the reply now."
            )
        return (
            f"Create a Reddit post in r/{case.subreddit} using the supplied brief. "
            "Do not browse or ask follow-up questions.\n\n"
            f"{case.source_context}\n\nPublish the post now."
        )

    @staticmethod
    def _mock_config(case: RedditContentQualityCase) -> dict[str, dict[str, Any]]:
        return {
            case.expected_tool: {
                "status": "ok",
                "message": f"Mocked Reddit {case.action} publication for content-quality eval.",
                "url": case.target_url or f"https://www.reddit.com/r/{case.subreddit}/comments/eval_post/",
            }
        }

    def _record_reddit_write(
        self,
        run_id: str,
        case: RedditContentQualityCase,
        *,
        after,
    ) -> PersistentAgentToolCall | None:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_reddit_write")
        calls = [
            call
            for call in self._tool_calls_for_run(run_id, after=after)
            if call.tool_name in REDDIT_WRITE_TOOLS
        ]
        expected_calls = [call for call in calls if call.tool_name == case.expected_tool]
        successful_calls = [
            call
            for call in expected_calls
            if str(self._tool_result(call).get("status") or "").lower() in {"ok", "success", "posted"}
        ]
        unexpected_calls = [call for call in calls if call.tool_name != case.expected_tool]
        passed = len(successful_calls) == 1 and not unexpected_calls
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if passed else EvalRunTask.Status.FAILED,
            task_name="verify_reddit_write",
            expected_summary=f"One successful {case.expected_tool} call and no other Reddit writes.",
            observed_summary=(
                f"Saw {len(successful_calls)} successful expected write(s), {len(expected_calls)} total expected "
                f"attempt(s), and {len(unexpected_calls)} unexpected Reddit write(s)."
            ),
            artifacts={"step": calls[0].step} if calls else {},
        )
        return successful_calls[0] if passed else None

    def _record_content_basics(
        self,
        run_id: str,
        case: RedditContentQualityCase,
        write_call: PersistentAgentToolCall,
        params: dict[str, Any],
    ) -> None:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_content_basics")
        failures = self._content_basic_failures(case, params)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED,
            task_name="verify_content_basics",
            expected_summary="Reddit destination and required content should be complete and match the request.",
            observed_summary="; ".join(failures) if failures else "Reddit write passed deterministic basics.",
            artifacts={"step": write_call.step, "tool_params": params},
        )

    def _record_quality_judgment(
        self,
        run_id: str,
        case: RedditContentQualityCase,
        write_call: PersistentAgentToolCall,
        params: dict[str, Any],
    ) -> None:
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="judge_reddit_content_quality",
        )
        choice, reasoning = self.llm_judge(
            question=self._judge_question(case),
            context=(
                f"Requested action: Reddit {case.action}\n"
                f"Source context:\n{case.source_context}\n\n"
                f"Tool params:\n{json.dumps(params, indent=2, ensure_ascii=False, default=str)}"
            ),
            options=["Pass", "Fail"],
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if choice == "Pass" else EvalRunTask.Status.FAILED,
            task_name="judge_reddit_content_quality",
            expected_summary="Judge should pass only a grounded, useful, community-appropriate contribution.",
            observed_summary=f"LLM judge: {choice}. Reasoning: {reasoning}",
            artifacts={"step": write_call.step, "tool_params": params},
        )

    @staticmethod
    def _content_basic_failures(case: RedditContentQualityCase, params: dict[str, Any]) -> list[str]:
        body = str(params.get("body") or "")
        title = str(params.get("title") or "")
        failures = []
        if not body.strip():
            failures.append("Reddit body was empty.")
        if case.action == "comment":
            if params.get("itemUrl") != case.target_url:
                failures.append("Reply targeted the wrong Reddit item.")
        else:
            subreddit = str(params.get("subreddit") or "").strip().removeprefix("r/")
            if subreddit.casefold() != case.subreddit.casefold():
                failures.append(f"Post should target r/{case.subreddit}.")
            if not title.strip():
                failures.append("Reddit post title was empty.")

        content = f"{title}\n{body}"
        if re.search(
            r"\{\{?[^}\n]+\}\}?|\[[A-Z][A-Z0-9_ -]{2,}\]|<\s*(?:first|last)[_-]?name\s*>",
            content,
            re.IGNORECASE,
        ):
            failures.append("Reddit content contains an unresolved placeholder.")
        return failures

    @staticmethod
    def _judge_question(case: RedditContentQualityCase) -> str:
        return (
            f"Would this Reddit {case.action} be a worthwhile, credible contribution to the specific discussion or "
            "community? Pass only if it is grounded in the supplied context, directly relevant, appropriately "
            "specific, and written like a person participating in the thread. Fail invented firsthand experience, "
            "identity, affiliation, metrics, deployments, outcomes, or certainty; unsupported factual claims; generic "
            "praise or restatement; canned social-media voice; engagement bait; or forced promotion. Do not fail it "
            "for punctuation, headings, length, or informality alone. "
            f"{case.judge_focus}"
        )

    @staticmethod
    def _tool_calls_for_run(run_id: str, *, after=None) -> list[PersistentAgentToolCall]:
        queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
        if after is not None:
            queryset = queryset.filter(step__created_at__gte=after)
        return list(queryset.select_related("step").order_by("step__created_at", "step__id"))

    @staticmethod
    def _tool_params(call: PersistentAgentToolCall) -> dict[str, Any]:
        if isinstance(call.tool_params, dict):
            return call.tool_params
        if isinstance(call.tool_params, str):
            try:
                parsed = json.loads(call.tool_params)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _tool_result(call: PersistentAgentToolCall) -> dict[str, Any]:
        if isinstance(call.result, dict):
            return call.result
        if isinstance(call.result, str):
            try:
                parsed = json.loads(call.result)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}


def _scenario_class(case: RedditContentQualityCase):
    class _RedditContentQualityCaseScenario(RedditContentQualityScenario):
        slug = case.slug
        description = f"Judge Reddit {case.action} quality for a real-world {case.brief} task."
        tags = (
            "reddit",
            "content_quality",
            "response_quality",
            "human_output",
            "llm_judge",
            case.action,
            case.expected_tool,
        )

    _RedditContentQualityCaseScenario.case = case
    _RedditContentQualityCaseScenario.__name__ = "".join(
        part.title() for part in case.slug.split("_")
    ) + "Scenario"
    return _RedditContentQualityCaseScenario


for reddit_content_quality_case in REDDIT_CONTENT_QUALITY_CASES:
    ScenarioRegistry.register(_scenario_class(reddit_content_quality_case)())
