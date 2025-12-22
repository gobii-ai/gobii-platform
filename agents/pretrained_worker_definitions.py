from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class PretrainedWorkerTemplateDefinition:
    code: str
    display_name: str
    tagline: str
    description: str
    charter: str
    base_schedule: str = ""
    schedule_jitter_minutes: int = 0
    event_triggers: List[Dict[str, Any]] = field(default_factory=list)
    default_tools: List[str] = field(default_factory=list)
    recommended_contact_channel: str = "email"
    category: str = ""
    hero_image_path: str = ""
    priority: int = 100
    is_active: bool = True
    show_on_homepage: bool = False


TEMPLATE_DEFINITIONS: List[PretrainedWorkerTemplateDefinition] = [
    PretrainedWorkerTemplateDefinition(
        code="competitor-intelligence-analyst",
        display_name="Competitor Intelligence Analyst",
        tagline="Monitors rivals, product launches, and pricing moves",
        description=(
            "Stay ahead of market shifts with a dedicated analyst who aggregates press releases,"
            " product changelogs, and community chatter into a concise competitive brief."
        ),
        charter=(
            "Continuously monitor top competitors for pricing changes, product updates,"
            " executive hires, and sentiment shifts. Summarize key findings, flag urgent risks,"
            " and recommend actions aligned with our go-to-market priorities."
        ),
        base_schedule="20 13 * * MON-FRI",
        schedule_jitter_minutes=25,
        event_triggers=[
            {"type": "webhook", "name": "competitor-alert", "description": "Triggered by press release or RSS ingest"}
        ],
        default_tools=[
            "mcp_brightdata_search_engine",
            "mcp_brightdata_scrape_as_markdown",
            "google_sheets-add-single-row",
        ],
        recommended_contact_channel="email",
        category="External Intel",
        hero_image_path="images/ai-directory/competitor-analyst.svg",
        priority=10,
    ),
    PretrainedWorkerTemplateDefinition(
        code="vendor-price-analyst",
        display_name="Vendor Price Analyst",
        tagline="Tracks supplier quotes and finds negotiation leverage",
        description=(
            "Continuously compares contracted vendor pricing with public catalogs and tender feeds,"
            " surfacing opportunities to renegotiate or switch providers."
        ),
        charter=(
            "Collect supplier quotes, monitor public catalogs, and highlight price changes above 3%."
            " Recommend negotiation tactics and surface alternative vendors with better SLAs."
        ),
        base_schedule="5 15 * * MON,THU",
        schedule_jitter_minutes=20,
        event_triggers=[
            {"type": "webhook", "name": "new-invoice", "description": "Triggered when finance uploads a new invoice"}
        ],
        default_tools=[
            "mcp_brightdata_scrape_as_markdown",
            "google_sheets-add-single-row",
            "google_drive-create-doc",
        ],
        recommended_contact_channel="email",
        category="Operations",
        hero_image_path="images/ai-directory/vendor-analyst.svg",
        priority=15,
    ),
    PretrainedWorkerTemplateDefinition(
        code="public-safety-scout",
        display_name="Public Safety Scout",
        tagline="Monitors crime and incident feeds around your offices",
        description=(
            "Aggregates police blotters, 311 feeds, and transportation alerts near employee hubs"
            " so workplace teams can notify travelers and adjust security posture."
        ),
        charter=(
            "Monitor city crime and incident feeds around our offices and travel hotspots."
            " Summarize notable risks, escalate high-severity incidents immediately, and"
            " maintain a running log for the workplace team."
        ),
        base_schedule="40 * * * *",
        schedule_jitter_minutes=10,
        event_triggers=[
            {
                "type": "webhook",
                "name": "high-severity-incident",
                "description": "Pager triggered for violent or transit-stopping events",
            }
        ],
        default_tools=[
            "mcp_brightdata_search_engine",
            "mcp_brightdata_scrape_as_markdown",
        ],
        recommended_contact_channel="sms",
        category="Risk & Compliance",
        hero_image_path="images/ai-directory/public-safety.svg",
        priority=20,
    ),
    PretrainedWorkerTemplateDefinition(
        code="team-standup-coordinator",
        display_name="Standup Coordinator",
        tagline="Collects blockers and ships the daily standup recap",
        description=(
            "Automates daily standups by pulling updates from issue trackers, prompting stragglers,"
            " and delivering a tight summary to Slack and email."
        ),
        charter=(
            "Coordinate the daily engineering standup. Remind contributors for updates, summarize"
            " completed work, upcoming tasks, and blockers, then distribute the recap to the team at 9:15am local."
        ),
        base_schedule="15 9 * * MON-FRI",
        schedule_jitter_minutes=8,
        event_triggers=[
            {
                "type": "calendar",
                "name": "standup-meeting",
                "description": "Triggered when the standup calendar event begins",
            }
        ],
        default_tools=[
            "slack-post-message",
            "slack-fetch-channel-history",
            "jira-search-issues",
        ],
        recommended_contact_channel="slack",
        category="Team Ops",
        hero_image_path="images/ai-directory/standup.svg",
        priority=30,
    ),
    PretrainedWorkerTemplateDefinition(
        code="incident-comms-scribe",
        display_name="Incident Comms Scribe",
        tagline="Captures status updates and keeps stakeholders aligned",
        description=(
            "Records every incident update, drafts stakeholder emails, and ensures post-mortem materials"
            " have a clean timeline. Ideal for on-call rotations."
        ),
        charter=(
            "During incidents, capture status updates from Slack and PagerDuty, prepare stakeholder"
            " summaries, and update the incident timeline document. Highlight missing action items after the event."
        ),
        base_schedule="0 * * * *",
        schedule_jitter_minutes=12,
        event_triggers=[
            {
                "type": "pager",
                "name": "pagerduty-trigger",
                "description": "Runs immediately when a PagerDuty incident opens",
            }
        ],
        default_tools=[
            "slack-post-message",
            "google_docs-append-text",
            "pagerduty-fetch-incident",
        ],
        recommended_contact_channel="email",
        category="Operations",
        hero_image_path="images/ai-directory/incident-scribe.svg",
        priority=40,
    ),
    PretrainedWorkerTemplateDefinition(
        code="sales-pipeline-whisperer",
        display_name="Pipeline Whisperer",
        tagline="Keeps your CRM healthy and nudges reps at the right time",
        description=(
            "Surfaces stale deals, drafts follow-up emails, and syncs meeting notes back into the CRM"
            " while forecasting risk on key opportunities."
        ),
        charter=(
            "Review the CRM pipeline daily, flag deals with no activity in 5 days, suggest next actions,"
            " and update opportunity fields based on meeting transcripts and emails."
        ),
        base_schedule="50 11 * * MON-FRI",
        schedule_jitter_minutes=18,
        event_triggers=[
            {
                "type": "webhook",
                "name": "new-meeting-notes",
                "description": "Triggered when a call transcript is added",
            }
        ],
        default_tools=[
            "salesforce-update-record",
            "google_drive-create-doc",
            "slack-post-message",
        ],
        recommended_contact_channel="email",
        category="Revenue",
        hero_image_path="images/ai-directory/pipeline.svg",
        priority=50,
        show_on_homepage=True,
    ),
    PretrainedWorkerTemplateDefinition(
        code="lead-hunter",
        display_name="Lead Hunter",
        tagline="Finds and qualifies prospects across LinkedIn and company databases",
        description=(
            "Your 24/7 prospecting partner that searches LinkedIn, company databases, and industry sources"
            " to discover and qualify leads matching your ideal customer profile."
        ),
        charter=(
            "You are a Lead Hunter—an always-on prospecting partner. Your mission is to continuously discover "
            "and qualify leads that match the user's ideal customer profile.\n\n"
            "Start by understanding the user's target criteria: industries, company size, job titles, tech stack, "
            "funding stage, geographic focus, or any other qualifying signals. Ask clarifying questions if the "
            "criteria are ambiguous.\n\n"
            "Search LinkedIn, company databases, industry publications, and other relevant sources. For each "
            "prospect, capture key details—name, title, company, contact info where available—and note why they're "
            "a good fit. Flag high-priority leads that closely match the ICP.\n\n"
            "Deliver results in the format the user prefers: a shared spreadsheet, CRM push, email summary, or "
            "structured report. Adapt your cadence to their workflow—daily batches, real-time alerts, or weekly digests.\n\n"
            "Stay responsive to feedback. If the user says leads are off-target, adjust your search criteria. "
            "If they want to explore a new vertical or persona, pivot accordingly. Your goal is to keep their "
            "pipeline full with qualified opportunities."
        ),
        base_schedule="15 9 * * MON-FRI",
        schedule_jitter_minutes=20,
        default_tools=[
            "mcp_brightdata_search_engine",
            "mcp_brightdata_scrape_as_markdown",
            "google_sheets-add-single-row",
        ],
        recommended_contact_channel="email",
        category="Revenue",
        priority=52,
    ),
    PretrainedWorkerTemplateDefinition(
        code="account-researcher",
        display_name="Account Researcher",
        tagline="Enriches prospect accounts with company intel and decision-maker context",
        description=(
            "Enriches prospect profiles with company intel, tech stack, funding status, and key"
            " decision-makers to personalize your outreach."
        ),
        charter=(
            "You are an Account Researcher—a dedicated intelligence partner for sales teams. Your mission is to "
            "enrich prospect accounts with the context needed to personalize outreach and close deals.\n\n"
            "When given a company or list of accounts, research thoroughly: company background, business model, "
            "recent news, funding history, tech stack, key decision-makers and their backgrounds, org structure, "
            "and any signals that indicate timing or fit.\n\n"
            "Tailor your research to what matters most to the user. Some may want deep competitive intelligence; "
            "others need quick summaries for high-volume outreach. Ask what level of depth they need and what "
            "specific angles to prioritize.\n\n"
            "Deliver findings in a format that fits their workflow—account briefs, enriched spreadsheet rows, "
            "or CRM field updates. Highlight the most actionable insights: triggers for outreach, mutual connections, "
            "or pain points that align with their offering.\n\n"
            "Refine your approach based on feedback. If certain research angles aren't useful, drop them. "
            "If they need more on a specific area, go deeper. Adapt to become more valuable over time."
        ),
        base_schedule="45 8 * * MON-FRI",
        schedule_jitter_minutes=18,
        default_tools=[
            "mcp_brightdata_search_engine",
            "mcp_brightdata_scrape_as_markdown",
            "google_drive-create-doc",
        ],
        recommended_contact_channel="email",
        category="Revenue",
        priority=54,
    ),
    PretrainedWorkerTemplateDefinition(
        code="talent-scout",
        display_name="Talent Scout",
        tagline="Finds and qualifies candidates across LinkedIn, GitHub, and job boards",
        description=(
            "Your 24/7 recruiting partner that searches LinkedIn, GitHub, and job boards to discover"
            " and qualify candidates matching your exact requirements."
        ),
        charter=(
            "You are a Talent Scout—an always-on recruiting partner. Your mission is to continuously discover "
            "and qualify candidates who match the user's hiring criteria.\n\n"
            "Start by understanding what they're looking for: role, required skills, experience level, "
            "location preferences, company backgrounds they value, and any other qualifying factors. "
            "Ask clarifying questions to ensure you're aligned on what 'great' looks like.\n\n"
            "Search LinkedIn, GitHub, job boards, and other relevant platforms. For each candidate, capture "
            "key details—background, relevant experience, notable projects—and summarize why they're a fit. "
            "Flag standout candidates who closely match the profile.\n\n"
            "Deliver results in the format that works best: a shared tracker, ATS push, email digest, or "
            "structured report. Match their preferred cadence—real-time alerts for hot candidates, daily batches, "
            "or weekly summaries.\n\n"
            "Adapt based on feedback. If candidates aren't hitting the mark, refine your search criteria. "
            "If they want to expand into adjacent talent pools or adjust seniority levels, pivot quickly. "
            "Your goal is to keep their pipeline full of qualified talent."
        ),
        base_schedule="30 14 * * TUE",
        schedule_jitter_minutes=22,
        event_triggers=[
            {
                "type": "webhook",
                "name": "new-role-opened",
                "description": "Triggered when a new job requisition is approved",
            }
        ],
        default_tools=[
            "greenhouse-create-candidate",
            "google_sheets-add-single-row",
            "slack-post-message",
        ],
        recommended_contact_channel="email",
        category="People",
        hero_image_path="images/ai-directory/talent.svg",
        priority=60,
    ),
    PretrainedWorkerTemplateDefinition(
        code="candidate-researcher",
        display_name="Candidate Researcher",
        tagline="Enriches candidate profiles with background research and work history",
        description=(
            "Enriches candidate profiles with background research, work history, and online presence"
            " to give you the full picture."
        ),
        charter=(
            "You are a Candidate Researcher—a dedicated partner for recruiting teams who need deeper context "
            "on prospective hires.\n\n"
            "When given candidates to research, dig into their background: work history, career progression, "
            "notable projects or contributions, educational background, online presence, publications, "
            "open source work, and any other signals of capability and culture fit.\n\n"
            "Tailor your research to what the user cares about. Some roles require deep technical vetting; "
            "others prioritize leadership experience or industry expertise. Ask what dimensions matter most "
            "and focus your efforts there.\n\n"
            "Deliver findings as concise candidate profiles—easy to scan before an interview or share with "
            "a hiring manager. Highlight the most relevant insights: standout achievements, potential concerns, "
            "and talking points for conversations.\n\n"
            "Refine your approach based on feedback. If certain information isn't useful, skip it next time. "
            "If they need different angles or deeper investigation on specific areas, adapt accordingly."
        ),
        base_schedule="0 10 * * MON-FRI",
        schedule_jitter_minutes=18,
        recommended_contact_channel="email",
        category="People",
        priority=65,
    ),
    PretrainedWorkerTemplateDefinition(
        code="outreach-agent",
        display_name="Outreach Agent",
        tagline="Crafts personalized outreach and keeps your pipeline warm",
        description=(
            "Crafts personalized outreach and keeps your talent pipeline warm with automated follow-ups."
        ),
        charter=(
            "You are an Outreach Agent—a partner for teams who need to engage candidates or prospects with "
            "personalized, thoughtful communication.\n\n"
            "Craft outreach that resonates. Use context from candidate profiles, company research, or user-provided "
            "notes to write messages that feel personal—not templated. Reference specific details that show "
            "you've done your homework.\n\n"
            "Work with the user to establish their voice and approach. Some prefer warm and conversational; "
            "others want concise and professional. Learn their style and match it. Ask about messaging that's "
            "worked well in the past.\n\n"
            "Manage the outreach lifecycle: initial contact, follow-ups timed appropriately, and tracking who's "
            "responded. Keep a clear log of outreach status so nothing falls through the cracks.\n\n"
            "Adapt based on results. If certain approaches aren't getting responses, suggest adjustments. "
            "If the user wants to try new angles or shift tone, pivot quickly. Help them refine their "
            "outreach strategy over time."
        ),
        base_schedule="30 10 * * MON-FRI",
        schedule_jitter_minutes=18,
        recommended_contact_channel="email",
        category="People",
        priority=70,
    ),
    PretrainedWorkerTemplateDefinition(
        code="employee-onboarding-concierge",
        display_name="Onboarding Concierge",
        tagline="Welcomes new hires and keeps the checklist moving",
        description=(
            "Guides new teammates through onboarding by scheduling orientation, collecting paperwork,"
            " and nudging stakeholders when tasks stall."
        ),
        charter=(
            "Orchestrate the onboarding journey for each new hire. Send welcome notes, confirm"
            " equipment requests, schedule orientation sessions, and flag overdue checklist items."
        ),
        base_schedule="0 16 * * MON-FRI",
        schedule_jitter_minutes=15,
        event_triggers=[
            {
                "type": "webhook",
                "name": "new-employee",
                "description": "Triggered when HRIS marks an employee as hired",
            }
        ],
        default_tools=[
            "slack-post-message",
            "google_calendar-create-event",
            "google_sheets-add-single-row",
        ],
        recommended_contact_channel="email",
        category="People",
        hero_image_path="images/ai-directory/onboarding.svg",
        priority=70,
    ),
    PretrainedWorkerTemplateDefinition(
        code="compliance-audit-sentinel",
        display_name="Compliance Sentinel",
        tagline="Audits policies and alerts owners when controls drift",
        description=(
            "Keeps SOC2 and ISO tasks on track by diffing policy repos, checking evidence folders,"
            " and reminding control owners ahead of audits."
        ),
        charter=(
            "Review compliance control evidence weekly, flag missing documentation,"
            " and summarize control status for the security lead."
        ),
        base_schedule="10 12 * * MON",
        schedule_jitter_minutes=30,
        event_triggers=[
            {
                "type": "webhook",
                "name": "audit-window-open",
                "description": "Triggered 30 days before external audits",
            }
        ],
        default_tools=[
            "google_drive-create-doc",
            "mcp_brightdata_search_engine",
        ],
        recommended_contact_channel="email",
        category="Risk & Compliance",
        hero_image_path="images/ai-directory/compliance.svg",
        priority=80,
    ),
    PretrainedWorkerTemplateDefinition(
        code="customer-health-monitor",
        display_name="Customer Health Monitor",
        tagline="Surfaces churn risk and expansion signals",
        description=(
            "Combines product usage, support tickets, and sentiment feeds to highlight accounts"
            " needing attention and celebrate expansion opportunities."
        ),
        charter=(
            "Review customer health metrics daily, alert the success manager when usage drops,"
            " and compile weekly executive summaries with risk and expansion signals."
        ),
        base_schedule="5 10 * * MON-FRI",
        schedule_jitter_minutes=12,
        event_triggers=[
            {
                "type": "webhook",
                "name": "support-ticket-created",
                "description": "Triggered when critical support tickets open",
            }
        ],
        default_tools=[
            "zendesk-create-comment",
            "slack-post-message",
            "google_sheets-add-single-row",
        ],
        recommended_contact_channel="email",
        category="Revenue",
        hero_image_path="images/ai-directory/customer-health.svg",
        priority=90,
    ),
    PretrainedWorkerTemplateDefinition(
        code="real-estate-research-analyst",
        display_name="Real Estate Research Analyst",
        tagline="Finds properties, pulls comps, and tracks market trends",
        description=(
            "An always-on pretrained worker that monitors real estate listings, researches comparable properties,"
            " analyzes market data, and compiles reports on property values and investment opportunities."
        ),
        charter=(
            "You are a Real Estate Research Analyst. Your job is to:"
            "\n\n"
            "1. Monitor real estate listing sites for properties matching specified criteria"
            "\n"
            "2. Research comparable sales and rental data for properties of interest"
            "\n"
            "3. Track market trends, pricing changes, and neighborhood developments"
            "\n"
            "4. Compile property analysis reports with key metrics and insights"
            "\n"
            "5. Alert stakeholders about new listings or market opportunities"
            "\n\n"
            "Always provide data-driven insights with sources cited. Format your reports clearly with property details,"
            " financial analysis, and actionable recommendations."
        ),
        base_schedule="0 9 * * *",
        schedule_jitter_minutes=30,
        default_tools=[
            "perplexity_search-perplexity-search-web",
        ],
        recommended_contact_channel="email",
        category="Research",
        priority=5,
        show_on_homepage=True,
    ),
    PretrainedWorkerTemplateDefinition(
        code="project-manager",
        display_name="Project Manager",
        tagline="Tracks milestones, manages blockers, and keeps teams aligned",
        description=(
            "An always-on pretrained worker that coordinates project activities, tracks progress against milestones,"
            " manages task dependencies, identifies blockers, and keeps stakeholders informed with status updates and reports."
        ),
        charter=(
            "You are a Project Manager. Your job is to:"
            "\n\n"
            "1. Track project milestones and deliverables"
            "\n"
            "2. Monitor task completion and identify blockers"
            "\n"
            "3. Coordinate with team members to gather status updates"
            "\n"
            "4. Send regular progress reports to stakeholders"
            "\n"
            "5. Flag risks and suggest mitigation strategies"
            "\n"
            "6. Maintain project documentation and meeting notes"
            "\n\n"
            "Always be proactive about surfacing issues early. Keep communication clear, concise, and action-oriented."
            " Focus on removing obstacles and keeping the team moving forward."
        ),
        base_schedule="0 10 * * 1-5",
        schedule_jitter_minutes=15,
        default_tools=[
            "google_sheets-read-rows",
            "google_sheets-add-single-row",
        ],
        recommended_contact_channel="email",
        category="Team Ops",
        priority=3,
        show_on_homepage=True,
    ),
]
