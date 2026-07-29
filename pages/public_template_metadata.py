import re
from dataclasses import dataclass
from html import unescape

from django.utils.html import strip_tags


META_DESCRIPTION_MAX_LENGTH = 160
META_DESCRIPTION_MIN_CLAUSE_LENGTH = 80
SEO_TITLE_MAX_LENGTH = 60

_TERMINAL_PUNCTUATION = ".!?…。！？"
_DANGLING_DESCRIPTION_WORDS = frozenset(
    {
        "about",
        "across",
        "after",
        "against",
        "among",
        "and",
        "around",
        "as",
        "at",
        "before",
        "between",
        "but",
        "by",
        "during",
        "for",
        "from",
        "in",
        "including",
        "into",
        "like",
        "nor",
        "of",
        "on",
        "onto",
        "or",
        "over",
        "so",
        "than",
        "through",
        "to",
        "toward",
        "towards",
        "under",
        "via",
        "while",
        "with",
        "within",
        "without",
        "yet",
    }
)
_CLAUSE_BOUNDARY_PATTERN = re.compile(
    r"[,;:](?=\s)|\s[—–]\s|"
    r"\s(?:and|or|but|before|while|whereas|although|because|so that)\s",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class PublicTemplateSeoOverride:
    heading: str
    tagline: str
    description: str
    intro: str
    example_outputs: str


@dataclass(frozen=True)
class PublicTemplateMetadata:
    heading: str
    tagline: str
    social_title: str
    seo_title: str
    description: str
    intro: str


# These pages have unusually overlapping or long production presentation data.
# Overrides preserve their canonical records while giving searchers a clear,
# employee-first reason to choose one page over another.
PUBLIC_TEMPLATE_SEO_OVERRIDES = {
    "tpl-ab70c72148b9": PublicTemplateSeoOverride(
        heading="Capital Raise & Investor Relations AI Employee",
        tagline=(
            "Manage investor research, lead enrichment, compliance checks, outreach, "
            "and capital-raise reporting."
        ),
        description=(
            "Use this investor relations AI employee to research and enrich investor "
            "leads, verify compliance markers, coordinate outreach, and prepare reports."
        ),
        intro=(
            "This community AI employee supports an end-to-end capital-raise workflow: "
            "investor discovery and enrichment, regulatory-data checks, coordinated "
            "outreach, investor updates, and reporting."
        ),
        example_outputs=(
            "- Enriched investor lead lists\n"
            "- Regulatory-data review checklists\n"
            "- Human-reviewed outreach plans\n"
            "- Investor update and capital-raise reports"
        ),
    ),
    "tpl-f2c5bb1cdb34": PublicTemplateSeoOverride(
        heading="Professional Network Lead Hunter AI Employee",
        tagline=(
            "A community-built AI employee for lead discovery and qualification across "
            "professional networks."
        ),
        description=(
            "Use this community Lead Hunter AI employee to discover and qualify prospects "
            "across professional networks, then deliver reports or spreadsheet updates."
        ),
        intro=(
            "This community template focuses on finding and qualifying individual "
            "prospects from professional networks against a supplied ICP, then delivering "
            "structured reports or spreadsheet updates. For Gobii-maintained company "
            "research with account fit, buying signals, and source-backed reasoning, use "
            "the B2B Lead Research AI employee."
        ),
        example_outputs=(
            "- Qualified individual prospect lists\n"
            "- ICP match notes for each lead\n"
            "- Professional-network source links\n"
            "- Structured reports or spreadsheet updates"
        ),
    ),
    "tpl-2a3ec836a1cd": PublicTemplateSeoOverride(
        heading="Stripe Fraud & Dispute Monitoring AI Employee",
        tagline=(
            "Monitor Stripe chargebacks, early fraud warnings, and payment disputes "
            "with actionable alerts."
        ),
        description=(
            "Use this Stripe fraud and dispute monitoring AI employee to track "
            "chargebacks, fraud warnings, amounts, reasons, statuses, and review-ready alerts."
        ),
        intro=(
            "This official AI employee monitors Stripe chargebacks, payment disputes, "
            "and early fraud warnings. It organizes the transaction context and delivers "
            "review-ready alerts so finance teams can prioritize the cases that need action."
        ),
        example_outputs=(
            "- Chargeback and dispute alerts\n"
            "- Transaction IDs, amounts, reasons, and statuses\n"
            "- Early fraud-warning summaries\n"
            "- Prioritized review queues"
        ),
    ),
    "tpl-613e6c63700d": PublicTemplateSeoOverride(
        heading="Renewable Energy News Monitoring AI Employee",
        tagline=(
            "Monitor solar, wind, and energy-storage news and receive concise daily "
            "market updates."
        ),
        description=(
            "Use this renewable energy news monitoring AI employee for daily source-backed "
            "updates across solar, wind, storage, policy, and global market developments."
        ),
        intro=(
            "This community AI employee is designed for ongoing news monitoring. It "
            "tracks global developments in solar, wind, and energy storage, reviews the "
            "underlying sources, and prepares concise daily summaries."
        ),
        example_outputs=(
            "- Daily renewable-energy news briefs\n"
            "- Solar, wind, and storage development summaries\n"
            "- Source logs for policy and project updates\n"
            "- Notable-change alerts"
        ),
    ),
    "tpl-2e73efd36bee": PublicTemplateSeoOverride(
        heading="Renewable Energy Trend Reporting AI Employee",
        tagline=(
            "Identify renewable-energy market trends and turn the findings into visual, "
            "structured reports."
        ),
        description=(
            "Use this renewable energy trend reporting AI employee to identify emerging "
            "market patterns and produce structured research reports with data visualizations."
        ),
        intro=(
            "This community AI employee focuses on analytical reporting rather than daily "
            "news summaries. It identifies emerging renewable-energy market patterns and "
            "turns the findings into structured reports with data visualizations."
        ),
        example_outputs=(
            "- Emerging-trend reports\n"
            "- Market-pattern data visualizations\n"
            "- Segment and regional comparisons\n"
            "- Structured research findings"
        ),
    ),
    "tpl-c1f7eff8a2f5": PublicTemplateSeoOverride(
        heading="Web Research & Data Analysis AI Employee",
        tagline=(
            "Combine web research, scraping, and data analysis to answer complex research "
            "questions."
        ),
        description=(
            "Use this web research and data analysis AI employee to gather online evidence, "
            "scrape relevant sources, analyze data, and deliver source-backed insights."
        ),
        intro=(
            "This community AI employee is positioned for research-led analysis. It starts "
            "with a defined question, gathers relevant web evidence, and uses research, "
            "scraping, and data tools to produce deeper insights."
        ),
        example_outputs=(
            "- Source-backed research briefs\n"
            "- Scraped evidence tables\n"
            "- Data analyses and comparisons\n"
            "- Findings with cited sources"
        ),
    ),
    "tpl-12203bdb9209": PublicTemplateSeoOverride(
        heading="Conversational Research & File Analysis AI Employee",
        tagline=(
            "Move from an exploratory conversation to file processing, visualization, and "
            "practical data outputs."
        ),
        description=(
            "Use this conversational research AI employee to clarify a problem, process "
            "files, research supporting evidence, visualize data, and organize useful outputs."
        ),
        intro=(
            "This community AI employee is designed for flexible, conversation-led problem "
            "solving. It clarifies the goal first, then combines file processing, supporting "
            "web research, and data visualization to produce practical outputs."
        ),
        example_outputs=(
            "- Processed-file summaries\n"
            "- Clarified problem and execution plans\n"
            "- Supporting research notes\n"
            "- Data visualizations and organized outputs"
        ),
    ),
}

# These official and migrated high-value pages need editorial descriptions because
# their source copy cannot be shortened at a natural boundary near 160 characters.
PUBLIC_TEMPLATE_META_DESCRIPTION_OVERRIDES = {
    "account-research-ai-agent": (
        "Research target accounts, evaluate company fit and buying signals, and "
        "receive sales-ready briefs with source links and personalized outreach angles."
    ),
    "b2b-lead-research-agent": (
        "Find qualified B2B leads, evaluate company fit and buying signals, and "
        "receive a structured prospect list with sources, fit notes, and outreach angles."
    ),
    "tpl-f69de33885cf": (
        "Use this AI employee to identify and vet professionals across LinkedIn and "
        "Apollo, initiate outreach sequences, and log qualified candidates in your CRM."
    ),
    "tpl-2db8238181de": (
        "Use this AI employee to identify priority emails, synthesize key information, "
        "and add supporting research to a focused morning brief for the day ahead."
    ),
    "tpl-123d8d8489c7": (
        "Use this AI employee to find promotional opportunities, create platform-specific "
        "content, run social and SEO outreach, and maintain a placement log."
    ),
    "tpl-c0e7bc3a8b89": (
        "Use this AI employee to find energy startups and established firms seeking "
        "investment, then deliver structured fundraising data and visual market summaries."
    ),
    "tpl-62de76261e7a": (
        "Use this AI employee to map business architecture, automate project management, "
        "and maintain a central source of truth across multiple projects."
    ),
    "tpl-8a8d105a3a25": (
        "Use this AI employee to discover and vet talent, enrich professional profiles, "
        "and manage recruitment pipelines across multiple industries."
    ),
    "tpl-f5a569d2babb": (
        "Use this AI employee to monitor YouTube performance, analyze competitor channels "
        "and videos, and identify trends that grow views and engagement."
    ),
    "tpl-7b410745415f": (
        "Use this AI employee to simulate stock trades, monitor market news and momentum, "
        "and maintain a persistent record of paper-portfolio performance."
    ),
    "tpl-508ef3ad9f20": (
        "Use this AI employee to research prospects, warm them through LinkedIn and email, "
        "and hand engaged leads to sales after genuine multi-touch conversations."
    ),
    "tpl-3cc144f89d77": (
        "Use this AI employee to source sales candidates, enrich professional profiles, "
        "and expand SEO reach through large-scale link distribution."
    ),
}


def _clean_text(value: str | None) -> str:
    plain_text = unescape(strip_tags(str(value or ""))).replace("\xa0", " ")
    return re.sub(r"\s+", " ", plain_text).strip()


def _has_dangling_description_ending(value: str) -> bool:
    ending = value.rstrip(f" \t\r\n{_TERMINAL_PUNCTUATION},;:—–-")
    words = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", ending.casefold())
    return bool(words and words[-1] in _DANGLING_DESCRIPTION_WORDS)


def _finalize_description_clause(value: str) -> str | None:
    candidate = value.rstrip(" ,;:—–-")
    if not candidate or _has_dangling_description_ending(candidate):
        return None
    if candidate.endswith(tuple(_TERMINAL_PUNCTUATION)):
        return candidate
    return f"{candidate}."


def _complete_sentence_prefix(source: str) -> str | None:
    selected_sentences = []
    for sentence_match in re.finditer(
        r".+?[.!?…。！？](?=\s|$)",
        source,
    ):
        sentence = sentence_match.group(0).strip()
        candidate = " ".join([*selected_sentences, sentence])
        if len(candidate) > META_DESCRIPTION_MAX_LENGTH:
            break
        if _has_dangling_description_ending(candidate):
            break
        selected_sentences.append(sentence)
    return " ".join(selected_sentences) or None


def _complete_clause_prefix(source: str) -> str | None:
    prefix = source[: META_DESCRIPTION_MAX_LENGTH + 1]
    cutoffs = [
        match.start()
        for match in _CLAUSE_BOUNDARY_PATTERN.finditer(prefix)
        if match.start() >= META_DESCRIPTION_MIN_CLAUSE_LENGTH
    ]
    for cutoff in reversed(cutoffs):
        candidate = _finalize_description_clause(source[:cutoff])
        if candidate and len(candidate) <= META_DESCRIPTION_MAX_LENGTH:
            return candidate
    return None


def _shorten_description_source(source: str) -> str | None:
    if (
        len(source) <= META_DESCRIPTION_MAX_LENGTH
        and not _has_dangling_description_ending(source)
    ):
        return source

    return _complete_sentence_prefix(source) or _complete_clause_prefix(source)


def compose_meta_description(
    *,
    explicit_description: str | None,
    description: str | None,
    tagline: str | None,
    display_name: str | None,
) -> str:
    sources = []
    for value in (explicit_description, description, tagline):
        cleaned_value = _clean_text(value)
        if cleaned_value and cleaned_value not in sources:
            sources.append(cleaned_value)

    complete_long_source = None
    for source in sources:
        shortened_source = _shorten_description_source(source)
        if shortened_source:
            return shortened_source
        if (
            complete_long_source is None
            and not _has_dangling_description_ending(source)
        ):
            complete_long_source = _finalize_description_clause(source)

    if complete_long_source:
        return complete_long_source

    role_name = _clean_text(display_name) or "this role"
    return (
        f"Create a {role_name} AI employee from this reusable Gobii template "
        "and customize it for your workflow."
    )


def public_template_employee_link_name(display_name: str | None) -> str:
    cleaned_name = _clean_text(display_name)
    if not cleaned_name:
        return "this AI employee"
    if re.search(r"\bAI\s+Employee\b", cleaned_name, flags=re.IGNORECASE):
        return cleaned_name
    if re.search(r"\bAI\s+Agent\b", cleaned_name, flags=re.IGNORECASE):
        return re.sub(
            r"\bAI\s+Agent\b",
            "AI Employee",
            cleaned_name,
            flags=re.IGNORECASE,
        )
    return f"{cleaned_name} AI employee"


def public_template_employee_role_name(display_name: str | None) -> str:
    cleaned_name = _clean_text(display_name)
    role_name = re.sub(
        r"\s+(?:AI\s+)?(?:Agent|Employee)$",
        "",
        cleaned_name,
        flags=re.IGNORECASE,
    ).strip()
    return role_name or cleaned_name


def _employee_heading(display_name: str) -> str:
    cleaned_name = _clean_text(display_name)
    terminal_role_name = public_template_employee_role_name(cleaned_name)
    if terminal_role_name != cleaned_name:
        return f"{terminal_role_name} AI Employee"

    heading = re.sub(
        r"\bAI\s+Agent\b",
        "AI Employee",
        cleaned_name,
        flags=re.IGNORECASE,
    )
    if not re.search(r"\bAI\s+Employee\b", heading, flags=re.IGNORECASE):
        heading = f"{heading} AI Employee"
    return heading


def _compose_title(heading: str, *, omit_suffix: bool) -> tuple[str, str]:
    if omit_suffix:
        social_title = _clean_text(heading)
        return social_title, f"{social_title} | Gobii"

    social_title = f"{heading} Template"
    seo_title = f"{social_title} | Gobii"
    if len(seo_title) <= SEO_TITLE_MAX_LENGTH:
        return social_title, seo_title

    social_title = heading
    seo_title = f"{social_title} | Gobii"
    if len(seo_title) <= SEO_TITLE_MAX_LENGTH:
        return social_title, seo_title

    role_name = re.sub(
        r"\s+AI\s+Employee$",
        "",
        heading,
        flags=re.IGNORECASE,
    ).strip()
    return role_name, f"{role_name} | Gobii"


def get_public_template_seo_override(template) -> PublicTemplateSeoOverride | None:
    return PUBLIC_TEMPLATE_SEO_OVERRIDES.get(str(getattr(template, "code", "") or ""))


def public_template_library_name(template) -> str:
    override = get_public_template_seo_override(template)
    return override.heading if override else _clean_text(template.display_name)


def build_public_template_metadata(template) -> PublicTemplateMetadata:
    override = get_public_template_seo_override(template)
    template_code = str(getattr(template, "code", "") or "")
    display_name = _clean_text(template.display_name) or "Reusable Role"
    heading = override.heading if override else _employee_heading(display_name)
    tagline = override.tagline if override else _clean_text(template.tagline)
    intro = override.intro if override else _clean_text(template.description)
    omit_title_suffix = template.omit_ai_agent_template_title_suffix and not override
    social_title, seo_title = _compose_title(
        display_name if omit_title_suffix else heading,
        omit_suffix=omit_title_suffix,
    )
    description = compose_meta_description(
        explicit_description=(
            PUBLIC_TEMPLATE_META_DESCRIPTION_OVERRIDES.get(template_code)
            or (override.description if override else template.seo_meta_description)
        ),
        description=intro,
        tagline=tagline,
        display_name=display_name,
    )
    return PublicTemplateMetadata(
        heading=heading,
        tagline=tagline,
        social_title=social_title,
        seo_title=seo_title,
        description=description,
        intro=intro,
    )
