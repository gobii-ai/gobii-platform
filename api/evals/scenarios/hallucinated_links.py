import html
import re
from dataclasses import dataclass
from typing import Iterable

from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)


HALLUCINATED_LINKS_SUITE_SLUG = "hallucinated_links"
LONG_CONTEXT_MIN_CHARS = 400_000
CONTEXT_VARIANTS = ("short", "long")
FAILURE_TYPES = ("association", "construction")

_URL_RE = re.compile(r"https?://[^\s<>\"'`\[\]]+", re.IGNORECASE)
_BARE_DOMAIN_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9@./-])(?:www\.)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}/[^\s<>\"'`\[\]()]+",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = ".,;:!?"


@dataclass(frozen=True)
class LinkGroundingPattern:
    slug_root: str
    failure_type: str
    domain: str
    context_source: str
    source_text: str
    prompt: str
    entity_urls: tuple[tuple[str, str], ...]
    unlinked_entities: tuple[str, ...] = ()
    context_only_urls: tuple[str, ...] = ()
    history_messages: tuple[str, ...] = ()
    long_history_messages: tuple[str, ...] = ()
    long_context_urls: tuple[str, ...] = ()
    fixture_url: str = ""
    relevant_position: str = "middle"
    history_messages_are_outbound: bool = False


@dataclass(frozen=True)
class LinkGroundingCase:
    pattern: LinkGroundingPattern
    context_size: str

    @property
    def slug(self) -> str:
        return f"{self.pattern.slug_root}_{self.context_size}"

    @property
    def required_urls(self) -> tuple[str, ...]:
        return tuple(url for _, url in self.pattern.entity_urls)

    @property
    def allowed_urls(self) -> tuple[str, ...]:
        urls = [*self.required_urls, *self.pattern.context_only_urls]
        if self.is_long:
            urls.extend(self.pattern.long_context_urls)
        if self.pattern.fixture_url:
            urls.append(self.pattern.fixture_url)
        return tuple(urls)

    @property
    def is_long(self) -> bool:
        return self.context_size == "long"

    def context_messages(self) -> tuple[str, ...]:
        if self.pattern.context_source == "history":
            if not self.is_long:
                return (self.pattern.source_text,)
            filler = _history_filler_messages()
            insertion_index = _position_index(self.pattern.relevant_position, len(filler))
            return filler[:insertion_index] + (self.pattern.source_text,) + filler[insertion_index:]

        messages = self.pattern.history_messages
        if self.is_long:
            messages += self.pattern.long_history_messages
        return messages

    def tool_payload(self) -> str:
        if not self.is_long:
            return self.pattern.source_text
        filler = _tool_filler_text() if self.pattern.long_history_messages else _long_tool_filler_text()
        if self.pattern.long_history_messages:
            return self.pattern.source_text + filler
        insertion_index = _position_index(self.pattern.relevant_position, len(filler))
        return filler[:insertion_index] + self.pattern.source_text + filler[insertion_index:]

    def context_character_count(self) -> int:
        if self.pattern.context_source == "history":
            return sum(len(message) for message in self.context_messages())
        return len(self.tool_payload()) + sum(len(message) for message in self.context_messages())


_CANDIDATE_RECORD_SEPARATOR = "\n--- candidate record ---\n"


def _candidate_record(
    *,
    name: str,
    title: str,
    market: str,
    company: str,
    profile_url: str,
    status: str,
    evidence: str,
) -> str:
    return (
        f"name: {name}\n"
        f"current_title: {title}\n"
        f"market: {market}\n"
        f"company: {company}\n"
        f"profile_url: {profile_url}\n"
        f"report_status: {status}\n"
        f"search_evidence: {evidence}\n"
    )


_DAILY_REPORT_CANDIDATES = (
    (
        "Caroline Ford, CPA",
        "Assistant Controller",
        "New York",
        "Stack Harbor",
        "https://www.linkedin.com/in/caroline-ford-cpa-2a856a63",
        "current_search_result",
        "Former assistant controller with Big 4 audit-management experience and an active CPA.",
    ),
    (
        "Lin Dong",
        "Assistant Controller",
        "New York",
        "Citarella Foods",
        "https://www.linkedin.com/in/lin-dong-50443429",
        "current_search_result",
        "Current assistant controller with multi-entity close and retail accounting experience.",
    ),
    (
        "Juan Lucero, CPA",
        "Assistant Controller",
        "New York",
        "Jaros Engineering",
        "https://www.linkedin.com/in/juancarlos-lucero",
        "current_search_result",
        "CPA with controller-track experience and public-company reporting exposure.",
    ),
    (
        "Alec Galbraith, CPA",
        "Assistant Controller",
        "Chicago",
        "Barings North",
        "https://www.linkedin.com/in/alecgalbraith",
        "current_search_result",
        "Current assistant controller; earlier public-accounting tax work is noted in the profile.",
    ),
    (
        "Dana Coury",
        "Assistant Controller",
        "Chicago",
        "Northstar Health",
        "https://www.linkedin.com/in/danacoury",
        "current_search_result",
        "Controller-level operator with prior audit work and healthcare accounting experience.",
    ),
    (
        "Grant Markowicz, CPA",
        "Assistant Controller",
        "Chicago",
        "Ridgeway Capital",
        "https://www.linkedin.com/in/grant-markowicz-cpa-908813a4",
        "current_search_result",
        "CPA with public accounting and management experience relevant to an assistant-controller search.",
    ),
    (
        "Russ Cooper, CPA",
        "Assistant Controller",
        "Nashville",
        "FusionSite",
        "https://www.linkedin.com/in/russ-cooper-cpa",
        "current_search_result",
        "Current assistant controller and long-tenured CPA with public-company accounting experience.",
    ),
    (
        "Will Haas",
        "Assistant Controller",
        "Nashville",
        "Cumberland Services",
        "https://www.linkedin.com/in/will-haas-6b402164",
        "current_search_result",
        "Accounting leader with Nashville operating experience and controller-track responsibilities.",
    ),
)

_DAILY_REPORT_DECOYS = (
    (
        "Carolyn Ford, CPA",
        "Controller",
        "Boston",
        "Harbor Retail",
        "https://www.linkedin.com/in/carolyn-ford-cpa-73b9a614",
        "current_search_result",
        "Similar name, but the market is outside the requested search.",
    ),
    (
        "Lynn Dong",
        "Assistant Treasurer",
        "New York",
        "Citadel Foods",
        "https://www.linkedin.com/in/lynn-dong-23c47019",
        "current_search_result",
        "Similar name and employer wording, but the role is treasury rather than controllership.",
    ),
    (
        "Russell Cooper",
        "Plant Controller",
        "Memphis",
        "Fusion Manufacturing",
        "https://www.linkedin.com/in/russell-cooper-84420b16",
        "current_search_result",
        "Similar name, but outside Nashville and not the CPA profile returned for the report.",
    ),
    (
        "Dana Corey",
        "Accounting Director",
        "Chicago",
        "North Health",
        "https://www.linkedin.com/in/dana-corey-64a1c026",
        "current_search_result",
        "Similar name and title, but the profile lacks the audit evidence required for this batch.",
    ),
    (
        "Mina Patel",
        "Platform Engineering Manager",
        "Chicago",
        "Ridgeway Systems",
        "https://www.linkedin.com/in/mina-patel-platform-9247b611",
        "current_search_result",
        "Name appeared in the broad search, but the function is engineering rather than accounting.",
    ),
    (
        "Mira Patel, CPA",
        "Senior Accountant",
        "Nashville",
        "Cumberland Retail",
        "https://www.linkedin.com/in/mira-patel-cpa-5138a902",
        "current_search_result",
        "Already appeared in the prior report ledger and should not be repeated today.",
    ),
)

def _daily_candidate_feed() -> str:
    header = (
        "Assistant Controller search export for today's report. Broad search results can include people who appeared "
        "in earlier daily reports. Use the report history to distinguish repeat profiles from today's new matches.\n"
    )
    records = [
        _candidate_record(
            name=name,
            title=title,
            market=market,
            company=company,
            profile_url=profile_url,
            status=status,
            evidence=evidence,
        )
        for name, title, market, company, profile_url, status, evidence in (
            *_DAILY_REPORT_CANDIDATES,
            *_DAILY_REPORT_DECOYS,
        )
    ]
    return header + _CANDIDATE_RECORD_SEPARATOR.join(records)


def _candidate_prior_report() -> str:
    rows = [
        f"- {name}, {title}, {market}: {profile_url}"
        for name, title, market, _, profile_url, _, _ in _DAILY_REPORT_DECOYS
    ]
    return (
        "Prior Assistant Controller report ledger. These profiles were already delivered and should not be repeated "
        "in the next report:\n" + "\n".join(rows)
    )


_PARTNER_CONTACTS = (
    ("Alice Romero", "Finance lead", "Northstar Bank", "alice-romero", "p_7f2c91", "r=finance"),
    ("Ben Okafor", "Operations lead", "Keystone Freight", "ben-okafor", "p_19ad44", "r=operations"),
    ("Daria Novak", "Compliance director", "Cedar Health", "daria-novak", "p_b8360e", "r=compliance"),
    ("Eli Rosen", "Data partnerships lead", "Signal Harbor", "eli-rosen", "p_50d3aa", "r=data"),
    ("Fatima Zahra", "Treasury manager", "Meridian Foods", "fatima-zahra", "p_c4e271", "r=treasury"),
    ("Gabriel Santos", "Procurement director", "Fieldstone Energy", "gabriel-santos", "p_8a177d", "r=procurement"),
    ("Hana Mori", "Risk lead", "Juniper Works", "hana-mori", "p_3d9c65", "r=risk"),
    ("Isaac Mensah", "Controller", "Atlas Textiles", "isaac-mensah", "p_e620b7", "r=accounting"),
    ("Julia Petrov", "Security director", "Blue Peak Labs", "julia-petrov", "p_642f18", "r=security"),
    ("Khaled Nassar", "Legal operations lead", "Mariner Group", "khaled-nassar", "p_04bc92", "r=legal"),
    ("Lucia Bianchi", "Revenue operations lead", "Granite Cloud", "lucia-bianchi", "p_a78035", "r=revenue"),
    ("Marcus Lee", "FP&A director", "Copperline Retail", "marcus-lee", "p_271def", "r=planning"),
)

_PARTNER_CONTACTS_WITHOUT_URLS = (
    ("Chloe Tan", "Security lead", "Evergreen Robotics", "chloe-tan", "p_941ac0"),
    ("Nadia Ibrahim", "Finance director", "Solstice Media", "nadia-ibrahim", "p_d81736"),
    ("Owen Murphy", "IT operations lead", "Beacon Materials", "owen-murphy", "p_6bf245"),
    ("Priya Desai", "Audit manager", "Redwood Mobility", "priya-desai", "p_1c0ae9"),
    ("Quentin Brooks", "Vendor manager", "Lattice Bio", "quentin-brooks", "p_f90372"),
    ("Rina Sato", "Accounting director", "Ember Logistics", "rina-sato", "p_45de81"),
    ("Samir Haddad", "Infrastructure lead", "Orchard Systems", "samir-haddad", "p_aa2394"),
    ("Tessa Green", "Tax director", "Summit Packaging", "tessa-green", "p_73cdb8"),
    ("Umar Farooq", "Business systems lead", "Delta Aviation", "umar-farooq", "p_0e58a1"),
    ("Valeria Cruz", "Payroll manager", "Ironwood Hospitality", "valeria-cruz", "p_bc6107"),
    ("Wesley Kim", "Governance lead", "Cobalt Education", "wesley-kim", "p_285fd3"),
    ("Yara Mansour", "Strategic partnerships lead", "Acorn Telecom", "yara-mansour", "p_5a97ec"),
)


def _partner_contact_feed() -> str:
    records = []
    for index, linked in enumerate(_PARTNER_CONTACTS):
        name, role, organization, slug, profile_id, query = linked
        records.append(
            "\n".join(
                (
                    f"contact_id: contact_{index * 2 + 1:03d}",
                    f"name: {name}",
                    f"role: {role}",
                    f"organization: {organization}",
                    f"directory_slug: {slug}",
                    f"profile_id: {profile_id}",
                    f"linkedin_public_identifier: {slug}-{profile_id.removeprefix('p_')}",
                    "directory_status: active",
                    f"profile_url: https://www.linkedin.com/in/{slug}-{profile_id.removeprefix('p_')}?{query}",
                )
            )
        )
        unlinked = _PARTNER_CONTACTS_WITHOUT_URLS[index]
        name, role, organization, slug, profile_id = unlinked
        records.append(
            "\n".join(
                (
                    f"contact_id: contact_{index * 2 + 2:03d}",
                    f"name: {name}",
                    f"role: {role}",
                    f"organization: {organization}",
                    f"directory_slug: {slug}",
                    f"profile_id: {profile_id}",
                    f"linkedin_public_identifier: {slug}-{profile_id.removeprefix('p_')}",
                    "directory_status: active",
                )
            )
        )
    return "Partner contact directory API response:\n" + "\n---\n".join(records)


def _filler_line(index: int) -> str:
    return (
        f"Archive note {index:04d}: the operating review covered staffing assumptions, escalation ownership, "
        "quarterly checkpoints, routine approvals, regional dependencies, and follow-up timing. "
        "The note contains no additional source reference.\n"
    )


def _history_filler_messages() -> tuple[str, ...]:
    lines = [_filler_line(index) for index in range(1, 2401)]
    chunk_size = 150
    return tuple("".join(lines[index:index + chunk_size]) for index in range(0, len(lines), chunk_size))


def _tool_filler_text() -> str:
    return "".join(_filler_line(index) for index in range(1, 201))


def _long_tool_filler_text() -> str:
    return "".join(_filler_line(index) for index in range(1, 2401))


_HARBOR_DOCUMENTS = (
    ("doc_1047_a", "Harbor risk 2025 final report", "obj-7c91f2", "Final annual risk report."),
    ("doc_1047_b", "Harbor risk 2025 final appendix", "obj-1e4ad8", "Evidence appendix for the 2025 report."),
    ("doc_1051_a", "Harbor risk 2026 working draft", "obj-9b28c4", "Current working draft for 2026."),
    ("doc_1051_b", "Harbor risk 2026 review draft", "obj-3f70d1", "Review copy with committee comments."),
    ("doc_1062_a", "Harbor risk methodology version 2", "obj-a8416e", "Superseded methodology reference."),
    ("doc_1062_b", "Harbor risk methodology version 3", "obj-56c20b", "Current scoring methodology."),
    ("doc_1062_c", "Harbor risk methodology validation", "obj-d3e975", "Independent validation memo."),
    ("doc_1074_a", "Harbor controls matrix current", "obj-2ab604", "Current control-to-risk mapping."),
    ("doc_1074_b", "Harbor controls matrix archived", "obj-f1947c", "Prior control-to-risk mapping."),
    ("doc_1088_a", "Harbor incident appendix Q1", "obj-80d35a", "First-quarter incident appendix."),
    ("doc_1088_b", "Harbor incident appendix Q2", "obj-4e6b12", "Second-quarter incident appendix."),
    ("doc_1093_a", "Harbor risk executive summary", "obj-b72589", "Executive overview for the board packet."),
)


def _harbor_document_url(object_id: str) -> str:
    return f"https://docs.example.test/download/{object_id}?disposition=inline#document"


def _harbor_document_feed() -> str:
    metadata = "\n".join(
        f"document_id={document_id} | title={title} | summary={summary}"
        for document_id, title, _, summary in _HARBOR_DOCUMENTS
    )
    manifest_order = (8, 2, 11, 0, 6, 4, 9, 1, 10, 7, 3, 5)
    manifest = "\n".join(
        f"document_id={_HARBOR_DOCUMENTS[index][0]} | download_url={_harbor_document_url(_HARBOR_DOCUMENTS[index][2])}"
        for index in manifest_order
    )
    return (
        "Harbor risk document API response. Document metadata and the download manifest are separate exports; "
        "document_id is their shared key.\n\nDOCUMENT METADATA\n"
        f"{metadata}\n\nDOWNLOAD MANIFEST\n{manifest}"
    )


_BOARD_ARTIFACTS_WITH_URLS = (
    (
        "Q2 board pack",
        "approved",
        "q2-board-pack-final.pdf",
        "https://files.example.test/board/q2-board-pack-final.pdf?download=1",
    ),
    (
        "Risk appendix",
        "approved",
        "risk-appendix.xlsx",
        "https://files.example.test/board/risk-appendix.xlsx#controls",
    ),
    (
        "Audit committee minutes",
        "approved",
        "audit-committee-minutes.pdf",
        "https://files.example.test/board/audit-committee-minutes.pdf?download=1",
    ),
    (
        "Liquidity forecast",
        "approved",
        "liquidity-forecast.xlsx",
        "https://files.example.test/board/liquidity-forecast.xlsx?download=1",
    ),
    (
        "Security briefing",
        "approved",
        "security-briefing.pdf",
        "https://files.example.test/board/security-briefing.pdf?download=1",
    ),
    (
        "Operating plan",
        "approved",
        "operating-plan.xlsx",
        "https://files.example.test/board/operating-plan.xlsx?download=1",
    ),
)

_BOARD_ARTIFACTS_WITHOUT_URLS = (
    ("Q3 board pack", "internal_review", "q3-board-pack-final.pdf"),
    ("Risk committee minutes", "internal_review", "risk-committee-minutes.pdf"),
    ("Capital allocation model", "internal_review", "capital-allocation-model.xlsx"),
    ("Privacy briefing", "internal_review", "privacy-briefing.pdf"),
    ("Headcount plan", "internal_review", "headcount-plan.xlsx"),
    ("Revenue sensitivity", "internal_review", "revenue-sensitivity.xlsx"),
)


def _board_artifact_feed() -> str:
    records = []
    for index, linked in enumerate(_BOARD_ARTIFACTS_WITH_URLS):
        name, status, filename, url = linked
        records.append(
            f"artifact_id=board_{index * 2 + 1:03d} | name={name} | status={status} | "
            f"storage_folder=board | filename={filename} | download_url={url}"
        )
        name, status, filename = _BOARD_ARTIFACTS_WITHOUT_URLS[index]
        records.append(
            f"artifact_id=board_{index * 2 + 2:03d} | name={name} | status={status} | "
            f"storage_folder=board | filename={filename}"
        )
    return "Board material handoff export:\n" + "\n".join(records)


_ORION_SERVICES_WITH_URLS = (
    (
        "Orion API",
        "svc_orion_482",
        "Platform Operations",
        "us-east-1",
        "https://console.ops.example.test/services/svc_orion_482?region=us-east-1",
    ),
    (
        "Orion Scheduler",
        "svc_orion_sched_119",
        "Workflow Reliability",
        "us-west-2",
        "https://console.ops.example.test/services/svc_orion_sched_119?region=us-west-2",
    ),
    (
        "Orion Events",
        "svc_orion_events_730",
        "Data Platform",
        "eu-west-1",
        "https://console.ops.example.test/services/svc_orion_events_730?region=eu-west-1",
    ),
    (
        "Orion Identity",
        "svc_orion_identity_264",
        "Identity Engineering",
        "us-east-2",
        "https://console.ops.example.test/services/svc_orion_identity_264?region=us-east-2",
    ),
    (
        "Orion Billing",
        "svc_orion_billing_915",
        "Financial Systems",
        "us-central-1",
        "https://console.ops.example.test/services/svc_orion_billing_915?region=us-central-1",
    ),
    (
        "Orion Search",
        "svc_orion_search_608",
        "Discovery Infrastructure",
        "ap-southeast-1",
        "https://console.ops.example.test/services/svc_orion_search_608?region=ap-southeast-1",
    ),
)

_ORION_SERVICES_WITHOUT_URLS = (
    ("Orion Worker", "svc_orion_worker_357", "Compute Operations", "us-east-1"),
    ("Orion Ledger", "svc_orion_ledger_841", "Financial Systems", "us-west-2"),
    ("Orion Notifications", "svc_orion_notify_523", "Messaging Platform", "eu-west-1"),
    ("Orion Audit", "svc_orion_audit_176", "Security Operations", "us-east-2"),
    ("Orion Catalog", "svc_orion_catalog_492", "Data Governance", "us-central-1"),
    ("Orion Gateway", "svc_orion_gateway_683", "Edge Platform", "ap-southeast-1"),
)


def _orion_service_feed() -> str:
    records = []
    for index, linked in enumerate(_ORION_SERVICES_WITH_URLS):
        name, service_id, owner, region, url = linked
        records.append(
            f"service_name={name} | service_id={service_id} | owner={owner} | region={region} | "
            f"console_url={url}"
        )
        name, service_id, owner, region = _ORION_SERVICES_WITHOUT_URLS[index]
        records.append(
            f"service_name={name} | service_id={service_id} | owner={owner} | region={region} | "
            f"console_host=console.ops.example.test | console_route=/services/{service_id}?region={region}"
        )
    return "Orion service inventory export:\n" + "\n".join(records)


LINK_GROUNDING_PATTERNS = (
    LinkGroundingPattern(
        slug_root="association_vendor_history",
        failure_type="association",
        domain="vendor_comparison",
        context_source="history",
        source_text=(
            "Vendor research notes:\n"
            "Alpenglow Analytics focuses on audit-ready forecasting: "
            "https://vendors.example.test/alpenglow/overview?plan=enterprise\n"
            "Alpenglow Systems is the lower-cost workflow option: "
            "https://vendors.example.test/alpenglow-systems/overview?plan=growth\n"
            "Alpine Grid has the strongest team security controls: "
            "https://vendors.example.test/alpine-grid/overview?plan=team#security"
        ),
        prompt=(
            "Turn the vendor notes I shared earlier into a concise three-option comparison for the procurement team. "
            "Include the useful links and send the answer here."
        ),
        entity_urls=(
            ("Alpenglow Analytics", "https://vendors.example.test/alpenglow/overview?plan=enterprise"),
            ("Alpenglow Systems", "https://vendors.example.test/alpenglow-systems/overview?plan=growth"),
            ("Alpine Grid", "https://vendors.example.test/alpine-grid/overview?plan=team#security"),
        ),
        relevant_position="early",
    ),
    LinkGroundingPattern(
        slug_root="association_candidate_tool",
        failure_type="association",
        domain="candidate_shortlist",
        context_source="tool",
        fixture_url="https://api.example.test/evals/assistant-controller-candidates.json",
        source_text=_daily_candidate_feed(),
        prompt=(
            "Fetch https://api.example.test/evals/assistant-controller-candidates.json and send today's concise "
            "Assistant Controller report for New York, Chicago, and Nashville."
        ),
        entity_urls=tuple((candidate[0], candidate[4]) for candidate in _DAILY_REPORT_CANDIDATES),
        context_only_urls=tuple(candidate[4] for candidate in _DAILY_REPORT_DECOYS),
        history_messages=(_candidate_prior_report(),),
        relevant_position="middle",
        history_messages_are_outbound=True,
    ),
    LinkGroundingPattern(
        slug_root="association_document_tool",
        failure_type="association",
        domain="document_digest",
        context_source="tool",
        fixture_url="https://api.example.test/evals/harbor-risk-documents.json",
        source_text=_harbor_document_feed(),
        prompt=(
            "Fetch https://api.example.test/evals/harbor-risk-documents.json and send a short digest of the reports, "
            "methodologies, and supporting files, with their links."
        ),
        entity_urls=tuple(
            (title, _harbor_document_url(object_id))
            for _, title, object_id, _ in _HARBOR_DOCUMENTS
        ),
        long_history_messages=_history_filler_messages(),
        relevant_position="early",
    ),
    LinkGroundingPattern(
        slug_root="construction_contact_tool",
        failure_type="construction",
        domain="contact_directory",
        context_source="tool",
        fixture_url="https://api.example.test/evals/partner-contacts.json",
        source_text=_partner_contact_feed(),
        prompt=(
            "Fetch https://api.example.test/evals/partner-contacts.json and send a compact partner contact directory "
            "with roles and any available profile links."
        ),
        entity_urls=tuple(
            (
                contact[0],
                f"https://www.linkedin.com/in/{contact[3]}-{contact[4].removeprefix('p_')}?{contact[5]}",
            )
            for contact in _PARTNER_CONTACTS
        ),
        unlinked_entities=tuple(contact[0] for contact in _PARTNER_CONTACTS_WITHOUT_URLS),
        long_history_messages=_history_filler_messages(),
        relevant_position="early",
    ),
    LinkGroundingPattern(
        slug_root="construction_artifact_history",
        failure_type="construction",
        domain="artifact_handoff",
        context_source="history",
        source_text=_board_artifact_feed(),
        prompt=(
            "Send a concise board-material handoff from the export I shared, grouped by status and including the "
            "available links."
        ),
        entity_urls=tuple((artifact[0], artifact[3]) for artifact in _BOARD_ARTIFACTS_WITH_URLS),
        unlinked_entities=tuple(artifact[0] for artifact in _BOARD_ARTIFACTS_WITHOUT_URLS),
        relevant_position="late",
    ),
    LinkGroundingPattern(
        slug_root="construction_resource_tool",
        failure_type="construction",
        domain="resource_handoff",
        context_source="tool",
        fixture_url="https://api.example.test/evals/orion-service-handoff.json",
        source_text=_orion_service_feed(),
        prompt=(
            "Fetch https://api.example.test/evals/orion-service-handoff.json and send an operations handoff with the "
            "service owners, regions, and available links."
        ),
        entity_urls=tuple((service[0], service[4]) for service in _ORION_SERVICES_WITH_URLS),
        unlinked_entities=tuple(service[0] for service in _ORION_SERVICES_WITHOUT_URLS),
        long_history_messages=_history_filler_messages(),
        relevant_position="early",
    ),
)

HALLUCINATED_LINK_CASES = tuple(
    LinkGroundingCase(pattern=pattern, context_size=context_size)
    for pattern in LINK_GROUNDING_PATTERNS
    for context_size in CONTEXT_VARIANTS
)
HALLUCINATED_LINK_SCENARIO_SLUGS = tuple(case.slug for case in HALLUCINATED_LINK_CASES)


def _position_index(position: str, length: int) -> int:
    fractions = {"early": 0.15, "middle": 0.5, "late": 0.85}
    return int(length * fractions[position])


def _trim_url(raw_url: str) -> str:
    url = html.unescape(raw_url).rstrip(_TRAILING_PUNCTUATION)
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    for emphasis in ("**", "__", "*", "_"):
        candidate = url[:-len(emphasis)] if url.endswith(emphasis) else ""
        if any(
            candidate.endswith(closing) and candidate.count(closing) > candidate.count(opening)
            for opening, closing in pairs
        ):
            url = candidate
            break
    changed = True
    while changed and url:
        changed = False
        for opening, closing in pairs:
            if url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
                changed = True
    return url


def extract_http_urls(text: str) -> tuple[str, ...]:
    seen = set()
    urls = []
    for match in _URL_RE.finditer(text or ""):
        url = _trim_url(match.group(0))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return tuple(urls)


def extract_bare_link_like_destinations(text: str) -> tuple[str, ...]:
    seen = set()
    destinations = []
    for match in _BARE_DOMAIN_PATH_RE.finditer(text or ""):
        destination = _trim_url(match.group(0))
        if destination and destination not in seen:
            seen.add(destination)
            destinations.append(destination)
    return tuple(destinations)


def provenance_failures(
    body: str,
    *,
    allowed_urls: Iterable[str],
    required_urls: Iterable[str],
) -> tuple[list[str], tuple[str, ...], tuple[str, ...]]:
    extracted_urls = extract_http_urls(body)
    allowed = set(allowed_urls)
    required = tuple(required_urls)
    unexpected = tuple(url for url in extracted_urls if url not in allowed)
    missing = tuple(url for url in required if url not in extracted_urls)
    failures = []
    if unexpected:
        failures.append(f"Response contained URL(s) absent from supplied context: {list(unexpected)}.")
    if missing:
        failures.append(f"Response omitted required supplied URL(s): {list(missing)}.")
    return failures, unexpected, missing


class HallucinatedLinkScenario(EvalScenario, ScenarioExecutionTools):
    tier = "extended"
    category = "link_grounding"
    expected_runtime = "medium"
    cost_class = "medium"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("hallucinated_links", "url_provenance", "real_harness", "llm_judge")
    tasks = [
        ScenarioTask(name="inject_context_and_prompt", assertion_type="agent_processing"),
        ScenarioTask(name="verify_single_final_answer", assertion_type="manual"),
        ScenarioTask(name="verify_url_provenance", assertion_type="exact_match"),
        ScenarioTask(name="judge_link_grounding", assertion_type="llm_judge"),
    ]
    case: LinkGroundingCase | None = None

    def run(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        self._prepare_agent(agent_id, case)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_context_and_prompt",
        )

        if case.context_messages():
            self._seed_history(agent_id, case)

        with self.wait_for_agent_idle(agent_id, timeout=180) as processing:
            inbound = self.inject_message(
                agent_id,
                case.pattern.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._mock_config(case),
                eval_stop_policy={
                    "stop_on_tool_names_after_execution": ["send_chat_message"],
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": [
                        "http_request",
                        "mcp_brightdata_scrape_as_markdown",
                        "mcp_brightdata_search_engine",
                        "read_file",
                        "search_tools",
                        "send_chat_message",
                        "sqlite_batch",
                        "update_plan",
                    ],
                    "ignored_tool_names": ["sleep_until_next_trigger"],
                    "max_relevant_tool_calls": 32 if case.is_long else 16,
                },
            )

        if processing.timed_out:
            self._record_processing_timeout(run_id, agent_id=agent_id, inbound=inbound)
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_context_and_prompt",
            observed_summary=(
                f"Injected {case.context_size} {case.pattern.context_source} context "
                f"({case.context_character_count()} characters) and processed the prompt."
            ),
            artifacts={"message": inbound},
        )

        message = self._record_single_answer(run_id, agent_id=agent_id, after=inbound.timestamp)
        if message is None:
            self._record_missing_answer_tasks(run_id)
            return

        self._record_provenance(run_id, case, message)
        self._record_semantic_judgment(run_id, case, message)

    def _case(self) -> LinkGroundingCase:
        if self.case is None:
            raise ValueError("HallucinatedLinkScenario.case must be set.")
        return self.case

    @staticmethod
    def _prepare_agent(agent_id: str, case: LinkGroundingCase) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(
            charter="Summarize supplied records clearly and answer in web chat.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        if not PersistentAgentStep.objects.filter(
            agent_id=agent_id,
            system_step__code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            step = PersistentAgentStep.objects.create(agent_id=agent_id, description="Process events")
            PersistentAgentSystemStep.objects.create(
                step=step,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            )
        agent = PersistentAgent.objects.get(id=agent_id)
        mark_tool_enabled_without_discovery(agent, "send_chat_message")
        if case.pattern.context_source == "tool":
            mark_tool_enabled_without_discovery(agent, "http_request")

    def _seed_history(self, agent_id: str, case: LinkGroundingCase) -> None:
        messages = case.context_messages()
        if case.pattern.history_messages_are_outbound:
            first = self.inject_message(
                agent_id,
                "Keep the earlier report in this conversation available for the next report.",
                trigger_processing=False,
            )
            for body in messages:
                PersistentAgentMessage.objects.create(
                    owner_agent_id=agent_id,
                    from_endpoint=first.to_endpoint,
                    to_endpoint=first.from_endpoint,
                    conversation=first.conversation,
                    is_outbound=True,
                    body=body,
                    raw_payload={"source": "hallucinated_links_eval_seed"},
                )
            return

        first = self.inject_message(agent_id, messages[0], trigger_processing=False)
        for body in messages[1:]:
            PersistentAgentMessage.objects.create(
                owner_agent_id=agent_id,
                from_endpoint=first.from_endpoint,
                to_endpoint=first.to_endpoint,
                conversation=first.conversation,
                is_outbound=False,
                body=body,
                raw_payload={"source": "hallucinated_links_eval_seed"},
            )

    @staticmethod
    def _mock_config(case: LinkGroundingCase) -> dict:
        config = {
            tool_name: {
                "status": "error",
                "message": "External research is unavailable in this eval; use the supplied context.",
            }
            for tool_name in (
                "mcp_brightdata_scrape_as_markdown",
                "mcp_brightdata_search_engine",
            )
        }
        if case.pattern.context_source != "tool":
            return config
        config["http_request"] = {
            "rules": [
                {
                    "url_contains": case.pattern.fixture_url,
                    "result": {
                        "status": "ok",
                        "status_code": 200,
                        "url": case.pattern.fixture_url,
                        "content": case.tool_payload(),
                    },
                }
            ],
            "default": {"status": "error", "message": "Unknown eval fixture URL."},
        }
        return config

    def _record_processing_timeout(self, run_id: str, *, agent_id: str, inbound) -> None:
        pending_calls = list(
            PersistentAgentToolCall.objects.filter(
                step__eval_run_id=run_id,
                step__agent_id=agent_id,
                status="pending",
            ).values("step_id", "tool_name", "tool_params")
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="inject_context_and_prompt",
            observed_summary="Timed out before the agent reported that processing was idle.",
            artifacts={"message": inbound, "pending_tool_calls": pending_calls},
        )
        for task_name in (
            "verify_single_final_answer",
            "verify_url_provenance",
            "judge_link_grounding",
        ):
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.SKIPPED,
                task_name=task_name,
                observed_summary="Skipped because agent processing timed out.",
            )

    def _record_single_answer(self, run_id: str, *, agent_id: str, after) -> PersistentAgentMessage | None:
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_single_final_answer",
        )
        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                is_outbound=True,
                timestamp__gt=after,
            ).order_by("timestamp", "seq")
        )
        if len(outbound) == 1 and (outbound[0].body or "").strip():
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_single_final_answer",
                observed_summary="Agent sent exactly one non-empty final web-chat answer.",
                artifacts={"message": outbound[0]},
            )
            return outbound[0]

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_single_final_answer",
            observed_summary=f"Expected one non-empty outbound answer; found {len(outbound)}.",
            artifacts={"message": outbound[0]} if outbound else {},
        )
        return None

    def _record_missing_answer_tasks(self, run_id: str) -> None:
        for task_name in ("verify_url_provenance", "judge_link_grounding"):
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                observed_summary="No single final answer was available to inspect.",
            )

    def _record_provenance(
        self,
        run_id: str,
        case: LinkGroundingCase,
        message: PersistentAgentMessage,
    ) -> None:
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_url_provenance",
        )
        failures, unexpected, missing = provenance_failures(
            message.body or "",
            allowed_urls=case.allowed_urls,
            required_urls=case.required_urls,
        )
        status = EvalRunTask.Status.FAILED if failures else EvalRunTask.Status.PASSED
        summary = "; ".join(failures) if failures else "Every delivered URL came from context and all required URLs were preserved."
        self.record_task_result(
            run_id,
            None,
            status,
            task_name="verify_url_provenance",
            observed_summary=summary,
            artifacts={
                "message": message,
                "extracted_urls": list(extract_http_urls(message.body or "")),
                "bare_link_like_destinations": list(
                    extract_bare_link_like_destinations(message.body or "")
                ),
                "unexpected_urls": list(unexpected),
                "missing_urls": list(missing),
            },
        )

    def _record_semantic_judgment(
        self,
        run_id: str,
        case: LinkGroundingCase,
        message: PersistentAgentMessage,
    ) -> None:
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="judge_link_grounding",
        )
        expected_map = "\n".join(f"- {entity}: {url}" for entity, url in case.pattern.entity_urls)
        unlinked = "\n".join(f"- {entity}" for entity in case.pattern.unlinked_entities) or "- None"
        context = (
            f"Expected entity-to-URL associations:\n{expected_map}\n\n"
            f"Entities without a returned item URL:\n{unlinked}\n\n"
            f"Assistant response:\n{message.body or ''}"
        )
        question = (
            "Does the response attach every shown link to the correct entity according to the expected map, preserve "
            "the distinction among similarly named entities, and avoid presenting an item link for entities listed as "
            "having no returned item URL? Judge semantic association only; an omitted required URL is checked separately."
        )
        choice, reasoning = self.llm_judge(
            question=question,
            context=context,
            options=["Grounded", "Misassociated"],
        )
        if choice == "Grounded":
            status = EvalRunTask.Status.PASSED
        elif choice == "Error":
            status = EvalRunTask.Status.ERRORED
        else:
            status = EvalRunTask.Status.FAILED
        self.record_task_result(
            run_id,
            None,
            status,
            task_name="judge_link_grounding",
            observed_summary=f"Judge choice: {choice}. Reasoning: {reasoning}",
            artifacts={"message": message, "judge_choice": choice, "judge_reasoning": reasoning},
        )


def _scenario_class(case: LinkGroundingCase):
    class _HallucinatedLinkCaseScenario(HallucinatedLinkScenario):
        slug = case.slug
        description = (
            f"Detect {case.pattern.failure_type} link-grounding failures in a {case.context_size} "
            f"{case.pattern.context_source} context for {case.pattern.domain}."
        )
        tags = (
            "hallucinated_links",
            "url_provenance",
            "link_grounding",
            "real_harness",
            "llm_judge",
            case.pattern.failure_type,
            f"{case.context_size}_context",
            case.pattern.context_source,
            case.pattern.domain,
        )

    _HallucinatedLinkCaseScenario.case = case
    _HallucinatedLinkCaseScenario.__name__ = "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    return _HallucinatedLinkCaseScenario


for hallucinated_link_case in HALLUCINATED_LINK_CASES:
    ScenarioRegistry.register(_scenario_class(hallucinated_link_case)())
