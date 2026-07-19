import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.contrib.auth import get_user_model
from django.utils import timezone

from api.agent.comms.message_service import _ensure_participant, _get_or_create_conversation
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.agent.tools.web_chat_sender import WEB_CHAT_UNAVAILABLE_MESSAGE
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry, register_scenario
from api.models import (
    AgentCollaborator,
    CommsAllowlistEntry,
    CommsChannel,
    DeliveryStatus,
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentToolCall,
    build_web_user_address,
)
from api.services.web_sessions import start_web_session
from util.text_sanitizer import has_humanized_message_style_violation


MESSAGE_QUALITY_SUITE_SLUG = "message_quality_reports"
REPLY_CHANNEL_CONTINUITY_SLUG = "message_quality_reply_stays_in_web_chat"
UNAVAILABLE_WEB_CHANNEL_CONTINUITY_SLUG = "message_quality_unavailable_web_does_not_switch_channels"
FAILED_EMAIL_DELIVERY_RECOVERY_SLUG = "message_quality_failed_email_reports_failure"


@dataclass(frozen=True)
class MessageQualityCase:
    slug: str
    channel: str
    recipient: str
    subject: str
    brief: str
    source_facts: str
    source_example_ids: tuple[str, ...]
    quality_target: str = "rich_report"

    @property
    def expected_tool(self) -> str:
        if self.channel == "email":
            return "send_email"
        if self.channel == "chat":
            return "send_chat_message"
        raise ValueError(f"Unsupported message quality channel: {self.channel}")


REAL_WORLD_REPORT_FACTS = {
    "meme_trends": {
        "brief": "daily meme and viral trends summary",
        "source_example_ids": ("d6df8e11-6d20-42db-9029-e5fa9d664b2f",),
        "facts": (
            "Audience: social content operator.\n"
            "Report date: January 31, 2026.\n"
            "Findings: 11 trending memes from 2 sources; 10 TikTok memes and 1 Instagram meme.\n"
            "Sources: NapoleonCat contributed 10 TikTok items; BroBible contributed 1 Instagram meme.\n"
            "Opportunity levels: TikTok is high opportunity; Instagram is medium opportunity.\n"
            "Themes: relatable humor, pets, everyday situations.\n"
            "Next step: daily monitoring is scheduled for 10:09 AM UTC; tomorrow should dig into engagement metrics."
        ),
    },
    "colorist_sources": {
        "brief": "DaVinci Resolve colorist source map and tracking plan",
        "source_example_ids": ("c895601f-f747-43c1-9f60-13efe03f040a",),
        "facts": (
            "Audience: professional colorist named Victor.\n"
            "Tracked sources: 27 sources across 8 platforms.\n"
            "Platform counts: Website/Blog 8, GitHub 6, YouTube 5, Reddit 3, Forum 2, Instagram/Facebook 2.\n"
            "Trends: DCTLs are hot; 19 of 27 sources mention DCTLs, custom transforms, or free plugins.\n"
            "GitHub repos are high-value: xtremestuff/resolve-dctl and Demystify-Color/DCTLs are examples.\n"
            "YouTube tutorials dominate attention; r/colorists and r/davinciresolve surface working-colorist leads.\n"
            "Schedule: weekly Monday 9 AM UTC scan, with daily checks for YouTube and Reddit."
        ),
    },
    "price_monitor": {
        "brief": "weekly price monitor log",
        "source_example_ids": ("253f295e-68b8-4a49-9bd2-53981b5817e8",),
        "facts": (
            "Audience: retail operator named Samantha.\n"
            "Period: January 24-30, 2026.\n"
            "Total scans logged: 32.\n"
            "Unique ASINs monitored: 21 in the Top 50 tier.\n"
            "Deliverable: CSV log is ready at https://gobii.example.test/downloads/weekly-log.csv.\n"
            "Pending items: Best Deals Table is still needed for validation logic; email verification is still pending.\n"
            "Next run: regular 9 AM ET scan resumes Monday morning."
        ),
    },
    "quonset_leads": {
        "brief": "Quonset Business Park lead-scouting report",
        "source_example_ids": ("ff8a5029-1eb2-454b-bcac-dd7f2bb17515",),
        "facts": (
            "Audience: industrial sales operator named Tyrese.\n"
            "Scope: Quonset Business Park in Rhode Island.\n"
            "Companies identified: 142 from the tenant list PDF.\n"
            "Websites found: 7, which is 4.9% of companies.\n"
            "Emails verified: 2, which is 1.4% of companies.\n"
            "Verified leads: Agilent Technologies (vpl-customercare@example.test) and American Muscle Car Restorations (mike@example.test).\n"
            "Comparison: Devens has about 42% verified, Sterling has 75%, Quonset has 1.4%.\n"
            "Recommendation: Apollo.io authorization is the best next step; otherwise shift focus to higher-yield parks."
        ),
    },
    "trading_dashboard": {
        "brief": "AI trading system dashboard",
        "source_example_ids": ("2dd56685-4e91-4545-8ae7-6ddcc71fa091",),
        "facts": (
            "Audience: AI trading-system owner.\n"
            "Status date: March 26, 2026.\n"
            "System mode: AUTO-PILOT, phase 4 of 4.\n"
            "Connectivity: Alpaca Paper active; Polygon.io two-year backfill complete; Regime-Aware v2 signal engine live.\n"
            "Balances: Alpaca paper balance and equity are $99,830.90; buying power is $199,661.80; virtual tracked balance is $300.\n"
            "Market snapshot: BTCUSD $71,305 up 1.10%; ETHUSD $2,168.19 up 0.58%; SOLUSD $91.66 up 0.99%.\n"
            "Signals last 30 days: BUY 12 avg confidence 0.513, HOLD 11 avg confidence 0.113, SELL 7 avg confidence 0.513.\n"
            "Performance: total return -15.46%, max drawdown -16.34%, peak return +0.70%.\n"
            "Next steps: stress-test system, deploy PPO training, connect signals to Alpaca execution after virtual balance setup."
        ),
    },
}


def _case(slug_suffix: str, channel: str, recipient: str, subject: str, facts_key: str) -> MessageQualityCase:
    facts = REAL_WORLD_REPORT_FACTS[facts_key]
    return MessageQualityCase(
        slug=f"message_quality_{channel}_{slug_suffix}",
        channel=channel,
        recipient=recipient,
        subject=subject,
        brief=facts["brief"],
        source_facts=facts["facts"],
        source_example_ids=facts["source_example_ids"],
    )


REPORT_MESSAGE_QUALITY_CASES = (
    _case("meme_trends", "email", "creator@example.test", "Daily Meme & Viral Trends Summary", "meme_trends"),
    _case("meme_trends", "chat", "web-user", "Daily Meme & Viral Trends Summary", "meme_trends"),
    _case("colorist_sources", "email", "victor@example.test", "Colorist Sources Tracking Plan", "colorist_sources"),
    _case("colorist_sources", "chat", "web-user", "Colorist Sources Tracking Plan", "colorist_sources"),
    _case("price_monitor", "email", "samantha@example.test", "Weekly Price Monitor Log", "price_monitor"),
    _case("price_monitor", "chat", "web-user", "Weekly Price Monitor Log", "price_monitor"),
    _case("quonset_leads", "email", "tyrese@example.test", "Quonset Business Park Scout Report", "quonset_leads"),
    _case("quonset_leads", "chat", "web-user", "Quonset Business Park Scout Report", "quonset_leads"),
    _case("trading_dashboard", "email", "trader@example.test", "AI Trading System Dashboard", "trading_dashboard"),
    _case("trading_dashboard", "chat", "web-user", "AI Trading System Dashboard", "trading_dashboard"),
)

SIMPLE_EMAIL_QUALITY_CASES = (
    MessageQualityCase(
        slug="message_quality_email_cold_outreach_intro",
        channel="email",
        recipient="maya.chen@example.test",
        subject="Quick intro from Ridge Analytics",
        brief="cold outreach intro to a finance lead",
        source_facts=(
            "Sender: Elena from Ridge Analytics.\n"
            "Recipient: Maya Chen, VP Finance at Northstar Labs.\n"
            "Context: Northstar Labs is hiring an Accounts Payable Manager.\n"
            "Relevant offer: Ridge Analytics flags unusual vendor spend and duplicate invoice risk for finance teams.\n"
            "Ask: whether Maya is open to a 15-minute intro next week.\n"
            "Constraint: do not imply a prior relationship or make unsupported customer claims."
        ),
        source_example_ids=(),
        quality_target="simple_email",
    ),
    MessageQualityCase(
        slug="message_quality_email_cold_outreach_partner",
        channel="email",
        recipient="jordan.rivera@example.test",
        subject="Partner idea for your RevOps clients",
        brief="cold outreach partner idea",
        source_facts=(
            "Sender: Priya from Atlas Workflow.\n"
            "Recipient: Jordan Rivera at Beacon RevOps.\n"
            "Context: Beacon RevOps advises B2B SaaS teams on onboarding and retention operations.\n"
            "Relevant offer: Atlas Workflow turns scattered onboarding notes into tracked implementation checklists.\n"
            "Ask: whether Jordan would be open to comparing notes for 20 minutes.\n"
            "Constraint: keep the ask low-pressure and do not include pricing."
        ),
        source_example_ids=(),
        quality_target="simple_email",
    ),
)

HUMAN_MESSAGE_QUALITY_CASES = (
    MessageQualityCase(
        slug="message_quality_chat_project_update",
        channel="chat",
        recipient="web-user",
        subject="Project update",
        brief="quick project update",
        source_facts=(
            "The user asked whether the billing migration is still on track.\n"
            "Current status: the data copy finished this morning.\n"
            "Remaining work: verify 12 edge-case invoices and switch traffic tomorrow at 10 AM ET.\n"
            "Risk: two invoices have unexpected tax rounding; Morgan is checking them now.\n"
            "No action is needed from the user."
        ),
        source_example_ids=(),
        quality_target="human_message",
    ),
    MessageQualityCase(
        slug="message_quality_chat_evidence_acknowledgement",
        channel="chat",
        recipient="web-user",
        subject="Regression evidence",
        brief="brief acknowledgement of evidence for an existing issue",
        source_facts=(
            "Quick heads-up: the two-hour regression run reproduced malformed source links again. "
            "I already logged it under existing issue 49 about source verification and fabricated URLs, "
            "so there is nothing for you to update or create. Just wanted you to know."
        ),
        source_example_ids=(),
        quality_target="human_reply",
    ),
)

OWNER_UPDATE_QUALITY_CASES = (
    MessageQualityCase(
        slug="message_quality_chat_owner_pipeline_update",
        channel="chat",
        recipient="web-user",
        subject="Pipeline update",
        brief="owner-facing recruiting pipeline update",
        source_facts=(
            "Audience: the owner of the recruiting agent.\n"
            "Pipeline: 18 candidates reviewed, 6 qualified, 3 awaiting recruiter review, and 2 interviews booked.\n"
            "Sources: 11 candidates came from LinkedIn and 7 came from referrals.\n"
            "Quality: qualified candidates average 8.4 out of 10; the target is 8.0.\n"
            "Blocker: four profiles are missing compensation expectations.\n"
            "Spend: $186 used from a $250 weekly sourcing budget.\n"
            "Next: verify compensation for the four blocked profiles and prepare the six qualified candidates for review."
        ),
        source_example_ids=(),
        quality_target="owner_update",
    ),
)

MESSAGE_QUALITY_CASES = (
    REPORT_MESSAGE_QUALITY_CASES
    + SIMPLE_EMAIL_QUALITY_CASES
    + HUMAN_MESSAGE_QUALITY_CASES
    + OWNER_UPDATE_QUALITY_CASES
)
MESSAGE_QUALITY_SCENARIO_SLUGS = (
    *(case.slug for case in MESSAGE_QUALITY_CASES),
    REPLY_CHANNEL_CONTINUITY_SLUG,
    UNAVAILABLE_WEB_CHANNEL_CONTINUITY_SLUG,
    FAILED_EMAIL_DELIVERY_RECOVERY_SLUG,
)


MESSAGE_TOOL_NAMES = {"send_email", "send_chat_message", "send_sms", "send_agent_message"}
EMAIL_ALLOWED_CONFIRMATION_TOOLS = {"send_chat_message"}
class MessageQualityScenario(EvalScenario, ScenarioExecutionTools):
    tier = "extended"
    category = "message_quality"
    expected_runtime = "medium"
    cost_class = "high"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("message_quality", "response_quality", "llm_judge", "send_email", "send_chat_message")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_expected_send_tool", assertion_type="manual"),
        ScenarioTask(name="verify_formatting_basics", assertion_type="manual"),
        ScenarioTask(name="judge_message_quality", assertion_type="llm_judge"),
    ]
    case: MessageQualityCase | None = None

    def run(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        self._prepare_agent_for_case(agent_id, case)
        mock_config = self._mock_config(case)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                self._prompt(case),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names_after_execution": [case.expected_tool],
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": self._allowed_tool_names(case),
                    "ignored_tool_names": ["update_plan"],
                    "max_relevant_tool_calls": 6,
                },
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            expected_summary=self._expected_delivery_summary(case),
            artifacts={"message": inbound, "source_example_ids": list(case.source_example_ids)},
        )

        send_call = self._record_expected_send_tool(run_id, case, after=inbound.timestamp)
        if send_call is None:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_formatting_basics",
                observed_summary="No expected send tool call to inspect.",
            )
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="judge_message_quality",
                observed_summary="No expected send tool call to judge.",
            )
            return

        body = self._message_body(case, send_call)
        self._record_formatting_basics(run_id, case, send_call, body)
        self._record_quality_judgment(run_id, case, send_call, body)

    def _case(self) -> MessageQualityCase:
        if self.case is None:
            raise ValueError("MessageQualityScenario.case must be set.")
        return self.case

    def _prepare_agent_for_case(self, agent_id: str, case: MessageQualityCase) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        mark_tool_enabled_without_discovery(agent, "send_email")
        mark_tool_enabled_without_discovery(agent, "send_chat_message")
        if case.channel == "email":
            CommsAllowlistEntry.objects.update_or_create(
                agent=agent,
                channel=CommsChannel.EMAIL,
                address=case.recipient,
                defaults={
                    "is_active": True,
                    "allow_inbound": True,
                    "allow_outbound": True,
                    "verified": True,
                },
            )

    def _prompt(self, case: MessageQualityCase) -> str:
        if case.quality_target == "simple_email":
            return (
                f"Send a cold outreach email to {case.recipient} with subject '{case.subject}'.\n\n"
                "Use only these details; do not browse, create files, or ask follow-up questions.\n\n"
                f"{case.source_facts}\n\n"
                "Send the email now."
            )
        if case.quality_target == "human_message":
            return (
                "Send me a quick chat update using only the details below. Do not browse, create files, "
                f"or ask follow-up questions.\n\n{case.source_facts}\n\nSend the update now."
            )
        if case.quality_target == "human_reply":
            return case.source_facts
        if case.quality_target == "owner_update":
            return (
                "How are things looking with the recruiting work right now? Give me the useful update "
                "using only the details below. Do not browse, create files, or ask follow-up questions.\n\n"
                f"{case.source_facts}\n\nSend me the update now."
            )

        if case.channel == "email":
            delivery_instruction = (
                f"Send an email to {case.recipient} with subject '{case.subject}'. "
                "Include the report below."
            )
        else:
            delivery_instruction = (
                "Send me the report below in this chat."
            )

        return (
            f"{delivery_instruction}\n\n"
            f"Report type: {case.brief}.\n"
            "Use only these facts; do not browse, create files, or ask follow-up questions.\n\n"
            f"{case.source_facts}\n\n"
            "Send the report now."
        )

    def _mock_config(self, case: MessageQualityCase) -> dict[str, dict[str, Any]] | None:
        if case.channel == "chat":
            return None
        return {
            case.expected_tool: {
                "status": "ok",
                "message": f"Mocked {case.expected_tool} delivery for message quality eval.",
                "message_id": f"eval-{case.slug}",
            }
        }

    def _record_expected_send_tool(self, run_id: str, case: MessageQualityCase, *, after) -> PersistentAgentToolCall | None:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_expected_send_tool")
        send_calls = [
            call
            for call in self._tool_calls_for_run(run_id, after=after)
            if call.tool_name in MESSAGE_TOOL_NAMES
        ]
        expected_calls = [call for call in send_calls if call.tool_name == case.expected_tool]
        successful_calls = [
            call
            for call in expected_calls
            if self._tool_result(call).get("skipped") is not True
            and str(self._tool_result(call).get("status") or "").lower() in {"ok", "queued", "sent", "success"}
        ]
        unexpected_calls = self._unexpected_message_calls(case, send_calls)

        if len(successful_calls) == 1 and not unexpected_calls:
            sent_message = self._sent_message_for_call(successful_calls[0])
            confirmation_count = len(self._allowed_confirmation_calls(case, send_calls))
            confirmation_note = (
                f" with {confirmation_count} web chat confirmation call(s)"
                if confirmation_count
                else ""
            )
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_expected_send_tool",
                observed_summary=(
                    f"Agent delivered once via {case.expected_tool} after {len(expected_calls)} attempt(s)"
                    f"{confirmation_note}."
                ),
                artifacts=self._task_artifacts(successful_calls[0], sent_message),
            )
            return successful_calls[0]

        summary = (
            f"Expected one successful {case.expected_tool} delivery; saw {len(successful_calls)} successful, "
            f"{len(expected_calls)} total attempts, and {len(unexpected_calls)} unexpected message calls."
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_expected_send_tool",
            observed_summary=summary,
            artifacts={"step": send_calls[0].step} if send_calls else {},
        )
        return None

    @staticmethod
    def _allowed_tool_names(case: MessageQualityCase) -> list[str]:
        tool_names = ["update_plan", case.expected_tool]
        if case.channel == "email":
            tool_names.extend(sorted(EMAIL_ALLOWED_CONFIRMATION_TOOLS))
            tool_names.append("sqlite_batch")
        return tool_names

    @staticmethod
    def _allowed_confirmation_calls(
        case: MessageQualityCase,
        send_calls: list[PersistentAgentToolCall],
    ) -> list[PersistentAgentToolCall]:
        if case.channel != "email":
            return []
        return [call for call in send_calls if call.tool_name in EMAIL_ALLOWED_CONFIRMATION_TOOLS]

    @staticmethod
    def _unexpected_message_calls(
        case: MessageQualityCase,
        send_calls: list[PersistentAgentToolCall],
    ) -> list[PersistentAgentToolCall]:
        allowed_tools = {case.expected_tool}
        if case.channel == "email":
            allowed_tools.update(EMAIL_ALLOWED_CONFIRMATION_TOOLS)
        return [call for call in send_calls if call.tool_name not in allowed_tools]

    @staticmethod
    def _tool_calls_for_run(run_id: str, *, after=None) -> list[PersistentAgentToolCall]:
        queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
        if after is not None:
            queryset = queryset.filter(step__created_at__gte=after)
        return list(queryset.select_related("step").order_by("step__created_at", "step__id"))

    def _record_formatting_basics(
        self,
        run_id: str,
        case: MessageQualityCase,
        send_call: PersistentAgentToolCall,
        body: str,
    ) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_formatting_basics")
        params = self._tool_params(send_call)
        failures = self._formatting_failures(case, params, body, send_call=send_call)
        sent_message = self._sent_message_for_call(send_call)
        if not failures:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_formatting_basics",
                observed_summary="Message passed deterministic delivery basics.",
                artifacts={
                    **self._task_artifacts(send_call, sent_message),
                    "body_preview": body[:1200],
                },
            )
            return True

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_formatting_basics",
            observed_summary="; ".join(failures),
            artifacts={
                **self._task_artifacts(send_call, sent_message),
                "body_preview": body[:1200],
            },
        )
        return False

    def _record_quality_judgment(
        self,
        run_id: str,
        case: MessageQualityCase,
        send_call: PersistentAgentToolCall,
        body: str,
    ) -> None:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="judge_message_quality")
        params = self._tool_params(send_call)
        sent_message = self._sent_message_for_call(send_call)
        choice, reasoning = self.llm_judge(
            question=self._judge_question(case),
            context=(
                f"Delivery channel: {case.channel}\n"
                f"Source facts:\n{case.source_facts}\n\n"
                f"Tool params:\n{json.dumps(params, indent=2, ensure_ascii=False, default=str)}\n\n"
                f"Message body:\n{body}"
            ),
            options=["Pass", "Fail"],
        )
        if self._judge_reasoning_is_unusable(reasoning):
            choice, reasoning = self.llm_judge(
                question=(
                    f"{self._judge_question(case)} Your reasoning must cite concrete formatting evidence "
                    "from the message body."
                ),
                context=(
                    f"Delivery channel: {case.channel}\n"
                    f"Source facts:\n{case.source_facts}\n\n"
                    f"Tool params:\n{json.dumps(params, indent=2, ensure_ascii=False, default=str)}\n\n"
                    f"Message body:\n{body}"
                ),
                options=["Pass", "Fail"],
            )

        status = EvalRunTask.Status.PASSED if choice == "Pass" else EvalRunTask.Status.FAILED
        self.record_task_result(
            run_id,
            None,
            status,
            task_name="judge_message_quality",
            expected_summary=self._expected_judge_summary(case),
            observed_summary=f"LLM judge: {choice}. Reasoning: {reasoning}",
            artifacts={
                **self._task_artifacts(send_call, sent_message),
                "body_preview": body[:1200],
            },
        )

    @staticmethod
    def _expected_delivery_summary(case: MessageQualityCase) -> str:
        if case.quality_target == "simple_email":
            return f"Agent should send a concise outreach email via {case.expected_tool}."
        if case.quality_target == "human_message":
            return f"Agent should send a natural, context-specific update via {case.expected_tool}."
        if case.quality_target == "human_reply":
            return f"Agent should acknowledge the user's note naturally via {case.expected_tool}."
        if case.quality_target == "owner_update":
            return f"Agent should send a polished, scannable owner update via {case.expected_tool}."
        return f"Agent should send a polished {case.channel} report via {case.expected_tool}."

    @staticmethod
    def _expected_judge_summary(case: MessageQualityCase) -> str:
        if case.quality_target == "simple_email":
            return "Judge should pass only restrained, professional outreach email formatting."
        if case.quality_target == "human_message":
            return "Judge should pass only a natural, specific message without assistant-like tells."
        if case.quality_target == "human_reply":
            return "Judge should pass only a concise, natural acknowledgement without evaluative padding."
        if case.quality_target == "owner_update":
            return "Judge should pass only a human, polished, decision-useful owner update."
        return "Judge should pass only polished, rich, readable report formatting."

    @staticmethod
    def _judge_reasoning_is_unusable(reasoning: str) -> bool:
        return len((reasoning or "").strip()) < 40

    @staticmethod
    def _judge_question(case: MessageQualityCase) -> str:
        if case.quality_target == "simple_email":
            return (
                "Does this email fit a simple professional cold outreach use case? Pass only if it is "
                "body-only HTML that sounds naturally written for this recipient and stays restrained: a greeting, one to three short paragraphs, a clear "
                "reason and ask, and an optional simple signoff. Fail if it looks like a report, newsletter, "
                "or marketing landing page; uses tables, metric blocks, status badges, emoji section labels, "
                "heavy colors, multiple headings, excessive visual styling, generic praise, canned assistant language, or a templated cadence."
            )
        if case.quality_target == "human_message":
            return (
                "Would this read as a natural message from a thoughtful person who knows this context? Pass only if it is "
                "specific, conversational, appropriately candid, and allowed to show natural personality or charm. Fail for canned acknowledgements, generic "
                "enthusiasm, request restatement, polished assistant cadence, symmetrical rhetoric, report formatting, "
                "template labels, or needless headings. Do not penalize expressive formatting in an explicitly requested report or dashboard."
            )
        if case.quality_target == "human_reply":
            return (
                "Does this sound like a person responding directly to a short operational note? Pass only if it is a concise, "
                "context-aware acknowledgement. Fail if it opens by evaluating or praising the result, adds a formulaic concession such as 'even if', "
                "says this is exactly the kind of evidence needed, restates the user's rationale or decision, or stacks polished "
                "assistant-style framing around a simple confirmation. Do not require exact wording; a short acknowledgement is enough."
            )
        if case.quality_target == "owner_update":
            return (
                "Does this feel like a polished update from a thoughtful operator to the owner of the work? "
                "Pass only if it stays human and direct while making the multi-part operational state easy to scan, "
                "with clear visual grouping and proportionate use of sections, bullets, a compact table, metric blocks, "
                "or status labels. Fail if it is a dense plain paragraph, a loose fact dump, needlessly over-formatted, "
                "or templated/corporate. Do not require any one specific Markdown element."
            )
        if case.channel == "email":
            return (
                "Does this email meet a high bar for formatting quality? Pass only if it is body-only HTML "
                "that would look polished in an inbox: clear hierarchy, good spacing, visually distinct "
                "report sections, tables or metric blocks where useful, and visible inline color for "
                "accents/status/table styling. Prefer tasteful emoji, icons, badges, or other status markers "
                "for section labels and important values. Fail if it is plain, cramped, Markdown-like, "
                "mostly generic headings/lists, lacks visual status/value encoding, or does not feel like a "
                "rich report."
            )
        return (
            "Does this web chat message meet a high bar for report formatting quality? Pass only if it has "
            "clear Markdown hierarchy, tasteful emoji or status labels, useful tables or metric blocks, bullets, "
            "and good spacing. Fail if it is plain prose, cramped, hard to scan, or not report-like."
        )

    @staticmethod
    def _tool_params(send_call: PersistentAgentToolCall) -> dict[str, Any]:
        params = send_call.tool_params
        if isinstance(params, dict):
            return params
        if isinstance(params, str):
            try:
                parsed = json.loads(params)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _tool_result(send_call: PersistentAgentToolCall) -> dict[str, Any]:
        result = send_call.result
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _message_body(self, case: MessageQualityCase, send_call: PersistentAgentToolCall) -> str:
        params = self._tool_params(send_call)
        if case.channel == "email":
            return str(params.get("mobile_first_html") or "")
        return str(params.get("body") or "")

    @staticmethod
    def _sent_message_for_call(send_call: PersistentAgentToolCall) -> PersistentAgentMessage | None:
        result = send_call.result
        if not result:
            return None
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return None
        if not isinstance(result, dict):
            return None
        message_id = result.get("message_id")
        if not message_id:
            return None
        try:
            UUID(str(message_id))
        except ValueError:
            if send_call.tool_name == "send_email":
                return MessageQualityScenario._persist_mocked_email_message(send_call, result)
            return None
        return PersistentAgentMessage.objects.filter(id=message_id).first()

    @staticmethod
    def _persist_mocked_email_message(
        send_call: PersistentAgentToolCall,
        result: dict[str, Any],
    ) -> PersistentAgentMessage | None:
        params = MessageQualityScenario._tool_params(send_call)
        to_address = str(params.get("to_address") or "").strip().lower()
        subject = str(params.get("subject") or "").strip()
        body = str(params.get("mobile_first_html") or "").strip()
        if not to_address or not subject or not body:
            return None

        step_id = str(send_call.step_id)
        existing = PersistentAgentMessage.objects.filter(
            owner_agent_id=send_call.step.agent_id,
            raw_payload__eval_tool_call_step_id=step_id,
        ).first()
        if existing is not None:
            return existing

        agent = send_call.step.agent
        from_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address=f"agent-{agent.id}@eval.local",
            defaults={"is_primary": True},
        )
        to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=to_address,
            defaults={"owner_agent": None},
        )
        conversation = _get_or_create_conversation(
            CommsChannel.EMAIL,
            to_address,
            owner_agent=agent,
        )
        _ensure_participant(
            conversation,
            from_endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.AGENT,
        )
        _ensure_participant(
            conversation,
            to_endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
        )

        now = timezone.now()
        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=from_endpoint,
            conversation=conversation,
            is_outbound=True,
            body=body,
            raw_payload={
                "subject": subject,
                "source": "eval_mock_send_email",
                "eval_tool_call_step_id": step_id,
                "mocked_message_id": result.get("message_id"),
            },
            latest_status=DeliveryStatus.DELIVERED,
            latest_sent_at=now,
            latest_delivered_at=now,
        )

        updated_result = {
            **result,
            "mocked_message_id": result.get("message_id"),
            "message_id": str(message.id),
            "message": "Mocked send_email delivery and persisted simulated email for eval audit.",
        }
        send_call.result = json.dumps(updated_result)
        send_call.save(update_fields=["result"])
        return message

    @staticmethod
    def _task_artifacts(
        send_call: PersistentAgentToolCall,
        sent_message: PersistentAgentMessage | None,
    ) -> dict[str, Any]:
        artifacts = {"step": send_call.step}
        if sent_message is not None:
            artifacts["message"] = sent_message
        return artifacts

    def _formatting_failures(
        self,
        case: MessageQualityCase,
        params: dict[str, Any],
        body: str,
        *,
        send_call: PersistentAgentToolCall | None = None,
    ) -> list[str]:
        failures = []
        if not body.strip():
            failures.append("Message body was empty.")
        if has_humanized_message_style_violation(body) or (
            case.channel == "email" and has_humanized_message_style_violation(str(params.get("subject") or ""))
        ):
            failures.append("Recipient-facing prose used prohibited dash punctuation or a double-hyphen workaround.")
        result = self._tool_result(send_call) if send_call is not None else {}
        if params.get("will_continue_work") is not False and result.get("auto_sleep_ok") is not True:
            failures.append("will_continue_work should be false for final report delivery.")

        if case.channel == "email":
            if params.get("to_address") != case.recipient:
                failures.append(f"send_email.to_address should be {case.recipient}.")
            if not params.get("subject"):
                failures.append("send_email.subject is missing.")
            if re.search(r"</?(?:html|head|body)\b", body, re.IGNORECASE):
                failures.append("Email body should not include html/head/body wrapper tags.")
            if re.search(r"^\s*\|.+\|\s*$", body, re.MULTILINE):
                failures.append("Email body should use HTML tables, not Markdown pipe tables.")
        return failures


@register_scenario
class FailedEmailDeliveryRecoveryScenario(EvalScenario, ScenarioExecutionTools):
    slug = FAILED_EMAIL_DELIVERY_RECOVERY_SLUG
    version = "1.0"
    description = "A failed email result should remain unfinished and be reported accurately in the active chat."
    tier = "core"
    category = "message_quality"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("message_quality", "tool_failure", "send_email", "send_chat_message")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_failed_delivery_recorded", assertion_type="manual"),
        ScenarioTask(name="verify_owner_notified", assertion_type="manual"),
    ]

    @staticmethod
    def _notice_reports_failure(body: str) -> bool:
        normalized = " ".join(str(body or "").replace("’", "'").lower().split())
        return "email" in normalized and any(
            phrase in normalized
            for phrase in (
                "could not send",
                "couldn't send",
                "did not send",
                "didn't send",
                "failed to send",
                "not delivered",
                "not sent",
                "provider rejected",
                "was rejected",
            )
        )

    def run(self, run_id: str, agent_id: str) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        mark_tool_enabled_without_discovery(agent, "send_email")
        mark_tool_enabled_without_discovery(agent, "send_chat_message")
        CommsAllowlistEntry.objects.update_or_create(
            agent=agent,
            channel=CommsChannel.EMAIL,
            address="ops@example.test",
            defaults={
                "is_active": True,
                "allow_inbound": True,
                "allow_outbound": True,
                "verified": True,
            },
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                "Email ops@example.test that today's maintenance window moved to 4:30 PM ET.",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config={
                    "send_email": {
                        "status": "failed",
                        "message": "The email provider rejected the message; nothing was delivered.",
                        "auto_sleep_ok": True,
                    },
                },
                eval_stop_policy={
                    "stop_on_tool_names_after_execution": ["send_chat_message"],
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": ["send_email", "send_chat_message", "sqlite_batch", "update_plan"],
                    "ignored_tool_names": ["sqlite_batch", "update_plan"],
                    "max_relevant_tool_calls": 4,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Email request injected and real agent processing completed.",
            artifacts={"message": inbound},
        )

        calls = MessageQualityScenario._tool_calls_for_run(run_id, after=inbound.timestamp)
        email_calls = [call for call in calls if call.tool_name == "send_email"]
        failed_results = [
            call
            for call in email_calls
            if str(MessageQualityScenario._tool_result(call).get("status") or "").lower()
            in {"error", "failed", "failure"}
        ]
        failures_recorded = bool(email_calls) and len(failed_results) == len(email_calls) and all(
            call.status == "error" for call in email_calls
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if failures_recorded else EvalRunTask.Status.FAILED,
            task_name="verify_failed_delivery_recorded",
            expected_summary="Failed delivery results should be persisted as errors, never completed sends.",
            observed_summary=(
                f"Saw {len(failed_results)}/{len(email_calls)} failed email result(s); "
                f"persisted statuses were {[call.status for call in email_calls]}."
            ),
            artifacts={"step": email_calls[0].step} if email_calls else {},
        )

        chat_calls = [call for call in calls if call.tool_name == "send_chat_message"]
        delivered_chats = [
            call
            for call in chat_calls
            if MessageQualityScenario._tool_result(call).get("skipped") is not True
            and str(MessageQualityScenario._tool_result(call).get("status") or "").lower()
            in {"ok", "sent", "success"}
        ]
        clear_notice = len(delivered_chats) == 1 and self._notice_reports_failure(
            str(MessageQualityScenario._tool_params(delivered_chats[0]).get("body") or "")
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if clear_notice else EvalRunTask.Status.FAILED,
            task_name="verify_owner_notified",
            expected_summary="Agent should tell the active-chat requester that the email was not delivered.",
            observed_summary=(
                "Agent accurately reported the failed email in web chat."
                if clear_notice
                else f"Saw {len(delivered_chats)} delivered chat notice(s), without one clear failure report."
            ),
            artifacts={"step": delivered_chats[0].step} if delivered_chats else {},
        )


@register_scenario
class ReplyChannelContinuityScenario(EvalScenario, ScenarioExecutionTools):
    slug = REPLY_CHANNEL_CONTINUITY_SLUG
    version = "1.0"
    description = "A reply to an active web conversation should not move to an older or preferred email thread."
    tier = "core"
    category = "message_quality"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("message_quality", "tool_choice", "send_chat_message", "reply_channel")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_web_chat_delivery", assertion_type="manual"),
        ScenarioTask(name="verify_no_cross_channel_reply", assertion_type="manual"),
    ]

    @staticmethod
    def _seed_email_context(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
        owner_email, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address="owner@example.test",
            defaults={"owner_agent": None},
        )
        agent_email, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            defaults={"address": f"agent-{agent.id}@eval.local", "is_primary": True},
        )
        CommsAllowlistEntry.objects.update_or_create(
            agent=agent,
            channel=CommsChannel.EMAIL,
            address=owner_email.address,
            defaults={
                "is_active": True,
                "allow_inbound": True,
                "allow_outbound": True,
                "verified": True,
                "can_configure": True,
            },
        )
        conversation = _get_or_create_conversation(
            CommsChannel.EMAIL,
            owner_email.address,
            owner_agent=agent,
        )
        _ensure_participant(
            conversation,
            owner_email,
            PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
        )
        _ensure_participant(
            conversation,
            agent_email,
            PersistentAgentConversationParticipant.ParticipantRole.AGENT,
        )
        prior_email = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=owner_email,
            to_endpoint=agent_email,
            conversation=conversation,
            is_outbound=False,
            body="Please keep the current outreach batch moving and send a few more when they are ready.",
            raw_payload={"subject": "Send a few more"},
        )
        PersistentAgentMessage.objects.filter(pk=prior_email.pk).update(
            timestamp=timezone.now() - timedelta(minutes=20),
        )
        return owner_email

    @staticmethod
    def _result(call: PersistentAgentToolCall) -> dict[str, Any]:
        return MessageQualityScenario._tool_result(call)

    @staticmethod
    def _seed_competing_web_contact(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
        observer = get_user_model().objects.create_user(
            username=f"reply-observer-{agent.id}@eval.local",
            email=f"reply-observer-{agent.id}@eval.local",
        )
        AgentCollaborator.objects.bulk_create([AgentCollaborator(agent=agent, user=observer)])
        observer_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(observer.id, agent.id),
        )
        start_web_session(agent, observer, source="eval-observer")
        return observer_endpoint

    def run(self, run_id: str, agent_id: str) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        mark_tool_enabled_without_discovery(agent, "send_email")
        mark_tool_enabled_without_discovery(agent, "send_chat_message")
        self._seed_email_context(agent)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "One more thing for this outreach batch only: lead with the self-serve value, mention that it works "
                    "with the tools people already use, and route unusually large opportunities to the enterprise team. "
                    "Got it? We can handle the queue separately after this reply."
                ),
                trigger_processing=False,
                eval_run_id=run_id,
            )
            observer_endpoint = self._seed_competing_web_contact(agent)
            PersistentAgent.objects.filter(id=agent_id).update(preferred_contact_endpoint=observer_endpoint)
            self.trigger_processing(
                agent_id,
                eval_run_id=run_id,
                mock_config={
                    "send_email": {
                        "status": "ok",
                        "message": "Mocked email delivery for reply-channel regression eval.",
                        "message_id": "eval-reply-channel-email",
                    },
                },
                eval_stop_policy={
                    "stop_on_tool_names_after_execution": ["send_email"],
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": [
                        "sqlite_batch",
                        "send_chat_message",
                        "send_email",
                        "update_plan",
                    ],
                    "ignored_tool_names": ["sqlite_batch", "update_plan"],
                    "max_relevant_tool_calls": 8,
                },
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary=(
                "Web prompt injected with an older email path and a different preferred, more recently active "
                "web contact available."
            ),
            artifacts={"message": inbound},
        )

        calls = MessageQualityScenario._tool_calls_for_run(run_id, after=inbound.timestamp)
        chat_calls = [call for call in calls if call.tool_name == "send_chat_message"]
        delivered_chats = [
            call
            for call in chat_calls
            if self._result(call).get("skipped") is not True
            and str(self._result(call).get("status") or "").lower() in {"ok", "sent", "success"}
        ]
        cross_channel_calls = [
            call
            for call in calls
            if call.tool_name in {"send_email", "send_sms", "send_agent_message"}
        ]
        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                is_outbound=True,
                timestamp__gt=inbound.timestamp,
            )
            .select_related("conversation", "to_endpoint")
            .order_by("timestamp", "id")
        )
        exact_requester_delivery = (
            len(delivered_chats) == 1
            and len(outbound) == 1
            and outbound[0].conversation_id == inbound.conversation_id
            and outbound[0].to_endpoint_id == inbound.from_endpoint_id
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if exact_requester_delivery else EvalRunTask.Status.FAILED,
            task_name="verify_web_chat_delivery",
            expected_summary="Agent should deliver one non-skipped reply to the exact latest web requester.",
            observed_summary=(
                "Delivered one reply in the requester's exact conversation."
                if exact_requester_delivery
                else (
                    f"Saw {len(delivered_chats)} delivered web reply/replies and {len(outbound)} outbound "
                    "message(s), but the requester endpoint/conversation did not match exactly."
                )
            ),
            artifacts=(
                {"message": outbound[0]}
                if outbound
                else ({"step": chat_calls[0].step} if chat_calls else {})
            ),
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if not cross_channel_calls else EvalRunTask.Status.FAILED,
            task_name="verify_no_cross_channel_reply",
            expected_summary="Agent should not move the reply to email, SMS, or a peer.",
            observed_summary=(
                "Agent kept the reply on the requester's web conversation."
                if not cross_channel_calls
                else f"Agent attempted cross-channel replies via {[call.tool_name for call in cross_channel_calls]}."
            ),
            artifacts={"step": cross_channel_calls[0].step} if cross_channel_calls else {},
        )


@register_scenario
class UnavailableWebChannelContinuityScenario(EvalScenario, ScenarioExecutionTools):
    slug = UNAVAILABLE_WEB_CHANNEL_CONTINUITY_SLUG
    version = "1.0"
    description = "An unavailable web reply should remain undelivered instead of silently moving to email."
    tier = "core"
    category = "message_quality"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("message_quality", "tool_failure", "send_chat_message", "reply_channel")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_web_failure_honored", assertion_type="manual"),
        ScenarioTask(name="verify_no_cross_channel_reply", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        agent = PersistentAgent.objects.get(id=agent_id)
        mark_tool_enabled_without_discovery(agent, "send_email")
        mark_tool_enabled_without_discovery(agent, "send_chat_message")
        email_endpoint = ReplyChannelContinuityScenario._seed_email_context(agent)
        PersistentAgent.objects.filter(id=agent_id).update(preferred_contact_endpoint=email_endpoint)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                "Could you summarize which outreach messages worked best and why? Please answer me here.",
                trigger_processing=False,
                eval_run_id=run_id,
            )
            self.trigger_processing(
                agent_id,
                eval_run_id=run_id,
                mock_config={
                    "send_chat_message": {
                        "status": "error",
                        "message": WEB_CHAT_UNAVAILABLE_MESSAGE,
                        "retryable": False,
                        "terminal_error": True,
                    },
                    "send_email": {
                        "status": "ok",
                        "message": "Mocked email delivery; using it would be a channel regression.",
                        "message_id": "eval-wrong-channel-email",
                    },
                },
                eval_stop_policy={
                    "stop_on_tool_names_after_execution": ["send_email"],
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": ["sqlite_batch", "send_chat_message", "send_email", "update_plan"],
                    "ignored_tool_names": ["sqlite_batch", "update_plan", "sleep_until_next_trigger"],
                    "max_relevant_tool_calls": 6,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Web request was processed with a synthetic expired-session failure and an older email path.",
            artifacts={"message": inbound},
        )

        calls = MessageQualityScenario._tool_calls_for_run(run_id, after=inbound.timestamp)
        chat_calls = [call for call in calls if call.tool_name == "send_chat_message"]
        honored_failure = (
            len(chat_calls) == 1
            and chat_calls[0].status == "error"
            and MessageQualityScenario._tool_params(chat_calls[0]).get("will_continue_work") is False
            and "Do not move this reply" in str(MessageQualityScenario._tool_result(chat_calls[0]).get("message") or "")
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if honored_failure else EvalRunTask.Status.FAILED,
            task_name="verify_web_failure_honored",
            observed_summary=(
                "Agent attempted the requested web reply once and received the unavailable-session contract."
                if honored_failure
                else f"Expected one failed web attempt; saw {len(chat_calls)}."
            ),
            artifacts={"step": chat_calls[0].step} if chat_calls else {},
        )

        cross_channel_calls = [
            call for call in calls if call.tool_name in {"send_email", "send_sms", "send_agent_message"}
        ]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if not cross_channel_calls else EvalRunTask.Status.FAILED,
            task_name="verify_no_cross_channel_reply",
            observed_summary=(
                "Agent left the unavailable web reply undelivered instead of changing media."
                if not cross_channel_calls
                else f"Agent changed channels via {[call.tool_name for call in cross_channel_calls]}."
            ),
            artifacts={"step": cross_channel_calls[0].step} if cross_channel_calls else {},
        )


def _scenario_class(case: MessageQualityCase):
    class _MessageQualityCaseScenario(MessageQualityScenario):
        slug = case.slug
        description = f"Judge {case.quality_target} formatting for {case.expected_tool} on a real-world {case.brief} task."
        tags = (
            "message_quality",
            "response_quality",
            "llm_judge",
            case.channel,
            case.expected_tool,
            case.quality_target,
        )

    _MessageQualityCaseScenario.case = case
    _MessageQualityCaseScenario.__name__ = "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    return _MessageQualityCaseScenario


for message_quality_case in MESSAGE_QUALITY_CASES:
    ScenarioRegistry.register(_scenario_class(message_quality_case)())
