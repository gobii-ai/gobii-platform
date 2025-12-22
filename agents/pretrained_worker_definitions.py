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
            "Search LinkedIn, company databases, and industry sources for prospects matching the criteria we provide."
            " Qualify matches against our ICP, capture contact details, and deliver a daily lead list with"
            " brief notes on fit and suggested next steps."
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
            "Research target accounts and their key decision-makers. Capture company background,"
            " tech stack, funding stage, growth signals, and relevant news. Summarize the findings"
            " in a concise brief that can be used for personalized outreach."
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
            "Search LinkedIn, GitHub, and job boards for candidates matching the criteria we provide."
            " Qualify matches, summarize fit, and keep a shared tracker updated for the recruiting team."
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
            "Gather background research, work history, and online presence details for target candidates."
            " Summarize key findings and deliver an at-a-glance profile for each candidate."
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
            "Draft personalized outreach for target candidates, schedule follow-ups, and keep a running"
            " log of outreach status for the recruiting team."
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
