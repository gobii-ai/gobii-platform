import re
from dataclasses import dataclass
from html import unescape

from django.utils.html import strip_tags


META_DESCRIPTION_MAX_LENGTH = 160
SEO_TITLE_MAX_LENGTH = 60


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


def _clean_text(value: str | None) -> str:
    plain_text = unescape(strip_tags(str(value or ""))).replace("\xa0", " ")
    return re.sub(r"\s+", " ", plain_text).strip()


def _truncate_at_word_boundary(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    candidate = value[: max_length + 1]
    if candidate[max_length:max_length + 1].isspace():
        return candidate[:max_length].rstrip()
    words = candidate[:max_length].rsplit(maxsplit=1)
    return words[0].rstrip() if len(words) > 1 else candidate[:max_length].rstrip()


def compose_meta_description(
    *,
    explicit_description: str | None,
    description: str | None,
    tagline: str | None,
    display_name: str | None,
) -> str:
    source = (
        _clean_text(explicit_description)
        or _clean_text(description)
        or _clean_text(tagline)
    )
    if not source:
        role_name = _clean_text(display_name) or "this role"
        source = (
            f"Create a {role_name} AI employee from this reusable Gobii template "
            "and customize it for your workflow."
        )

    if len(source) <= META_DESCRIPTION_MAX_LENGTH:
        return source

    complete_sentences = re.findall(r".+?(?:[.!?](?=\s|$)|$)", source)
    selected_sentences = []
    for sentence in complete_sentences:
        candidate = " ".join([*selected_sentences, sentence.strip()])
        if len(candidate) > META_DESCRIPTION_MAX_LENGTH:
            break
        selected_sentences.append(sentence.strip())
    if selected_sentences:
        return " ".join(selected_sentences)

    truncated = _truncate_at_word_boundary(
        source,
        META_DESCRIPTION_MAX_LENGTH - 1,
    ).rstrip(" ,;:-.!?")
    return f"{truncated}."


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
        seo_title = f"{social_title} | Gobii"
        if len(seo_title) <= SEO_TITLE_MAX_LENGTH:
            return social_title, seo_title
        shortened_title = _truncate_at_word_boundary(
            social_title,
            SEO_TITLE_MAX_LENGTH - len(" | Gobii"),
        )
        return social_title, f"{shortened_title} | Gobii"

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
    suffix = " AI Employee | Gobii"
    shortened_role = _truncate_at_word_boundary(
        role_name,
        SEO_TITLE_MAX_LENGTH - len(suffix),
    )
    social_title = f"{shortened_role} AI Employee"
    return social_title, f"{social_title} | Gobii"


def get_public_template_seo_override(template) -> PublicTemplateSeoOverride | None:
    return PUBLIC_TEMPLATE_SEO_OVERRIDES.get(str(getattr(template, "code", "") or ""))


def public_template_library_name(template) -> str:
    override = get_public_template_seo_override(template)
    return override.heading if override else _clean_text(template.display_name)


def build_public_template_metadata(template) -> PublicTemplateMetadata:
    override = get_public_template_seo_override(template)
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
            override.description if override else template.seo_meta_description
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
