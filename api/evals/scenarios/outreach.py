import html
import re
from dataclasses import dataclass

from api.agent.comms.message_service import _ensure_participant, _get_or_create_conversation
from api.agent.system_skills.defaults import OUTREACH_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import enable_system_skills
from api.evals.base import ScenarioTask
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.message_quality import MessageQualityScenario
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentToolCall,
)


OUTREACH_SUITE_SLUG = "outreach"


@dataclass(frozen=True)
class OutreachCase:
    slug: str
    recipient: str
    brief: str
    source_facts: str
    judge_focus: str
    domain: str
    prior_subject: str = ""
    prior_body: str = ""
    channel: str = "email"
    subject: str = ""
    quality_target: str = "outreach"
    source_example_ids: tuple[str, ...] = ()

    @property
    def expected_tool(self) -> str:
        return "send_email"

    @property
    def is_followup(self) -> bool:
        return bool(self.prior_body)


OUTREACH_CASES = (
    OutreachCase(
        slug="outreach_cold_sales_finance",
        recipient="maya.chen@example.test",
        brief="cold sales introduction to a finance leader",
        domain="cold_sales",
        source_facts=(
            "Sender: Elena from Ridge Analytics.\n"
            "Recipient: Maya Chen, VP Finance at Northstar Labs.\n"
            "Known context: Northstar Labs is hiring an Accounts Payable Manager.\n"
            "Relevant offer: Ridge Analytics flags unusual vendor spend and duplicate invoice risk for finance teams.\n"
            "Desired next step: ask whether Maya is open to a 15-minute introduction next week."
        ),
        judge_focus=(
            "For this cold-sales case, require a credible connection between the known hiring signal and the offer. "
            "Fail vague prospecting copy, false familiarity, or unsupported customer and performance claims."
        ),
    ),
    OutreachCase(
        slug="outreach_recruiting_candidate",
        recipient="alex.morgan@example.test",
        brief="recruiting outreach to a qualified candidate",
        domain="recruiting",
        source_facts=(
            "Sender: Mara, a recruiter at Cedar Stack.\n"
            "Recipient: Alex Morgan, Senior Data Engineer at Harbor Grid.\n"
            "Verified public evidence: Alex led Python streaming reliability work and maintains an open-source Kafka "
            "monitoring plugin.\n"
            "Role: Staff Data Platform Engineer at Cedar Stack, focused on production event systems and open to remote "
            "employees in the United States.\n"
            "Compensation and Alex's interest are unknown.\n"
            "Desired next step: ask whether Alex wants a short role overview."
        ),
        judge_focus=(
            "For this recruiting case, connect the verified experience to the role without inventing qualifications, "
            "interest, compensation, or a prior relationship. The invitation should be informative and low pressure."
        ),
    ),
    OutreachCase(
        slug="outreach_customer_success_recovery",
        recipient="rene.lopez@example.test",
        brief="customer-success outreach after a service issue",
        domain="customer_success",
        source_facts=(
            "Sender: Devon from Customer Success at SlateSync.\n"
            "Recipient: Rene Lopez, operations lead at Brightpath.\n"
            "Known issue: Brightpath's recent CSV import failed because the file had duplicate column names.\n"
            "Current status: SlateSync engineering shipped a fix and the import is ready to retry.\n"
            "Desired next step: offer to join Rene for a 15-minute retry session."
        ),
        judge_focus=(
            "For this customer-success case, acknowledge the concrete issue, state the resolution plainly, and make "
            "the next step easy. Fail promotional copy, blame, defensiveness, or an attempt to upsell."
        ),
    ),
    OutreachCase(
        slug="outreach_partnership_revops",
        recipient="jordan.rivera@example.test",
        brief="partnership pitch based on a specific audience fit",
        domain="partnership",
        source_facts=(
            "Sender: Priya from Atlas Workflow.\n"
            "Recipient: Jordan Rivera at Beacon RevOps.\n"
            "Known context: Beacon RevOps advises B2B SaaS teams on onboarding and retention operations.\n"
            "Relevant offer: Atlas Workflow turns scattered onboarding notes into tracked implementation checklists.\n"
            "Desired next step: ask whether Jordan is open to comparing notes for 20 minutes."
        ),
        judge_focus=(
            "For this partnership case, explain the specific audience and workflow overlap. Fail vague synergy language, "
            "one-sided promotion, invented partnership history, or pressure to commit."
        ),
    ),
    OutreachCase(
        slug="outreach_no_reply_followup",
        recipient="maya.chen@example.test",
        brief="follow-up after an unanswered finance outreach email",
        domain="followup",
        source_facts=(
            "Sender: Elena from Ridge Analytics.\n"
            "Recipient: Maya Chen, VP Finance at Northstar Labs.\n"
            "The prior email introduced Ridge Analytics after Northstar Labs began hiring an Accounts Payable Manager.\n"
            "New relevant context: Ridge Analytics has a short checklist for duplicate-invoice controls during AP team "
            "growth.\n"
            "Desired next step: ask whether Maya wants the checklist, while making it easy to decline."
        ),
        judge_focus=(
            "For this no-reply follow-up, require a useful new reason to respond and a graceful way to decline. Fail "
            "guilt, manufactured urgency, claims that the recipient read the first email, or a repeated original pitch."
        ),
        prior_subject="Duplicate invoice risk during AP growth",
        prior_body=(
            "<p>Hi Maya,</p>"
            "<p>I saw that Northstar Labs is hiring an Accounts Payable Manager. Ridge Analytics helps finance teams "
            "flag unusual vendor spend and duplicate invoice risk as AP operations grow.</p>"
            "<p>Would you be open to a 15-minute introduction next week?</p>"
            "<p>Thanks,<br>Elena</p>"
        ),
    ),
)

OUTREACH_SCENARIO_SLUGS = tuple(case.slug for case in OUTREACH_CASES)


class OutreachScenario(MessageQualityScenario):
    tier = "extended"
    category = "outreach"
    expected_runtime = "medium"
    cost_class = "high"
    owner = "agent-platform"
    area = "system_skills"
    tags = ("outreach", "system_skill", "human_output", "llm_judge", "send_email")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_expected_send_tool", assertion_type="manual"),
        ScenarioTask(name="verify_formatting_basics", assertion_type="manual"),
        ScenarioTask(name="judge_message_quality", assertion_type="llm_judge"),
    ]
    case: OutreachCase | None = None

    def _case(self) -> OutreachCase:
        if self.case is None:
            raise ValueError("OutreachScenario.case must be set.")
        return self.case

    def _prepare_agent_for_case(self, agent_id: str, case: OutreachCase) -> None:
        super()._prepare_agent_for_case(agent_id, case)
        agent = PersistentAgent.objects.get(id=agent_id)
        result = enable_system_skills(agent, [OUTREACH_SYSTEM_SKILL_KEY])
        if result.get("invalid"):
            raise ValueError(f"Could not enable Outreach system skill: {result}")
        if case.is_followup:
            self._seed_prior_message(agent, case)

    @staticmethod
    def _seed_prior_message(agent: PersistentAgent, case: OutreachCase) -> PersistentAgentMessage:
        existing = OutreachScenario._prior_message(agent.id, case.slug)
        if existing is not None:
            return existing

        from_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address=f"agent-{agent.id}@eval.local",
            defaults={"is_primary": True},
        )
        to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=case.recipient,
            defaults={"owner_agent": None},
        )
        conversation = _get_or_create_conversation(
            CommsChannel.EMAIL,
            case.recipient,
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
        return PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=from_endpoint,
            conversation=conversation,
            is_outbound=True,
            body=case.prior_body,
            raw_payload={
                "subject": case.prior_subject,
                "source": "eval_outreach_prior",
                "case_slug": case.slug,
            },
        )

    @staticmethod
    def _prior_message(agent_id, case_slug: str) -> PersistentAgentMessage | None:
        return (
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                raw_payload__source="eval_outreach_prior",
                raw_payload__case_slug=case_slug,
            )
            .order_by("timestamp", "seq")
            .first()
        )

    def _prompt(self, case: OutreachCase) -> str:
        action = "Send a follow-up email in the existing thread" if case.is_followup else "Send an outreach email"
        return (
            f"{action} to {case.recipient}.\n\n"
            "Use only these facts; do not browse, create files, or ask follow-up questions.\n\n"
            f"{case.source_facts}\n\n"
            "Send the email now."
        )

    @staticmethod
    def _expected_delivery_summary(case: OutreachCase) -> str:
        return "Agent should send one grounded outreach email through send_email."

    @staticmethod
    def _expected_judge_summary(case: OutreachCase) -> str:
        return "Judge should pass only grounded outreach that reads like a thoughtful human wrote it."

    @staticmethod
    def _judge_question(case: OutreachCase) -> str:
        return (
            "Would a discerning recipient believe a thoughtful human wrote this outreach email for this specific "
            "situation? Pass only if the subject and body are grounded in the supplied facts, direct, concise for the "
            "context, naturally phrased, and organized around one clear reason and usually one low-pressure next step. "
            "The message must preserve the sender's role without inventing familiarity, claims, evidence, recipient "
            "interest, or commitments. Fail generic mass-email copy, stiff or padded prose, hype, cliches, stock AI "
            "phrases, unresolved placeholders, em dashes, emoji, report-style headings or tables, decorative styling, "
            "multiple competing asks, or a subject that is misleading or detached from the message. "
            f"{case.judge_focus}"
        )

    def _formatting_failures(
        self,
        case: OutreachCase,
        params: dict,
        body: str,
        *,
        send_call: PersistentAgentToolCall | None = None,
    ) -> list[str]:
        failures = super()._formatting_failures(case, params, body, send_call=send_call)
        subject = str(params.get("subject") or "")
        unescaped_body = html.unescape(body)
        unescaped_subject = html.unescape(subject)

        if "—" in unescaped_body or "—" in unescaped_subject:
            failures.append("Outreach should not use em dashes.")
        if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", unescaped_body + unescaped_subject):
            failures.append("Outreach should not use emoji or decorative symbols.")
        if re.search(
            r"\{\{?[^}\n]+\}\}?|\[[A-Z][A-Z0-9_ -]{2,}\]|<\s*(?:first|last)[_-]?name\s*>",
            f"{unescaped_subject}\n{unescaped_body}",
            re.I,
        ):
            failures.append("Outreach contains an unresolved placeholder.")
        if re.search(r"<\s*(?:h[1-6]|table|thead|tbody|tr|td|th|ul|ol)\b", unescaped_body, re.I):
            failures.append("Outreach should not use report-style headings, tables, or lists.")
        if re.search(r"^\s{0,3}(?:#{1,6}\s+|(?:[-*+] |\d+\. ))", unescaped_body, re.MULTILINE):
            failures.append("Outreach should not use Markdown headings or lists.")
        if re.search(r"\s(?:style|class)\s*=", unescaped_body, re.I):
            failures.append("Outreach should not use decorative style or class attributes.")
        if not case.is_followup and re.match(r"\s*(?:re|fwd)\s*:", unescaped_subject, re.I):
            failures.append("Initial outreach should not use a fake reply or forward subject.")

        if case.is_followup and send_call is not None:
            prior = self._prior_message(send_call.step.agent_id, case.slug)
            expected_reply_id = str(prior.id) if prior is not None else ""
            if str(params.get("reply_to_message_id") or "") != expected_reply_id:
                failures.append("Follow-up outreach should reply in the seeded email thread.")

        return failures


def _scenario_class(case: OutreachCase):
    class _OutreachCaseScenario(OutreachScenario):
        slug = case.slug
        description = f"Judge human outreach quality for a real-world {case.brief} task."
        tags = (
            "outreach",
            "system_skill",
            "human_output",
            "llm_judge",
            "send_email",
            case.domain,
        )

    _OutreachCaseScenario.case = case
    _OutreachCaseScenario.__name__ = "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    return _OutreachCaseScenario


for outreach_case in OUTREACH_CASES:
    ScenarioRegistry.register(_scenario_class(outreach_case)())
