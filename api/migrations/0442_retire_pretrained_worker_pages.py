from django.db import migrations


MERGED_CUSTOMIZATION_NOTES = {
    "b2b-lead-research-agent": (
        "The former Lead Hunter workflow also supported daily batches, real-time alerts, or weekly digests, "
        "with results delivered to a spreadsheet, CRM workflow, email summary, or structured report. "
        "Choose the cadence and handoff that match your sales process."
    ),
    "account-research-ai-agent": (
        "The former Account Researcher workflow emphasized funding history, technology signals, decision-maker "
        "backgrounds, organization structure, and recent news. Add the research angles that matter to your sales motion "
        "and choose between quick summaries and deeper account briefs."
    ),
    "ai-agent-for-candidate-sourcing": (
        "The former Talent Scout and Talent Sourcer workflows are consolidated here. Choose real-time alerts, "
        "daily batches, or weekly recruiting-funnel reports, and refine adjacent talent pools as role, location, "
        "seniority, and experience criteria change."
    ),
}

FORCED_CANONICAL_TEMPLATE_FIELDS = {
    "ai-agent-for-candidate-sourcing": (
        "display_name",
        "tagline",
        "description",
        "seo_meta_description",
        "charter",
        "best_for",
        "example_outputs",
        "required_inputs",
        "how_it_works",
        "customization_notes",
        "expected_tools_summary",
        "default_tools",
    ),
}

CAPITAL_RAISE_PRIMARY_CODE = "tpl-ab70c72148b9"
CAPITAL_RAISE_DUPLICATE_CODE = "tpl-deb9721ca6f7"
CAPITAL_RAISE_PRIMARY_CONTENT = {
    "tagline": (
        "Manage investor research, lead enrichment, compliance checks, outreach, "
        "and capital-raise reporting."
    ),
    "description": (
        "This community AI employee supports an end-to-end capital-raise workflow. "
        "It identifies and enriches potential investor leads using professional and "
        "business sources, verifies relevant SEC compliance markers, coordinates "
        "multi-channel outreach, and maintains investor relations through updates "
        "and reporting."
    ),
}

EXACT_LEGACY_TEMPLATE_CATEGORIES = {
    "competitor-intelligence-analyst": "External Intel",
    "vendor-price-analyst": "Operations",
    "public-safety-scout": "Risk & Compliance",
    "team-standup-coordinator": "Team Ops",
    "incident-comms-scribe": "Operations",
    "sales-pipeline-whisperer": "Revenue",
    "employee-onboarding-concierge": "People",
    "compliance-audit-sentinel": "Risk & Compliance",
    "customer-health-monitor": "Revenue",
    "real-estate-research-analyst": "Research",
    "project-manager": "Team Ops",
}


TEMPLATES = (
    {
        "code": "b2b-lead-research-agent",
        "slug": "b2b-lead-research-agent",
        "display_name": "B2B Lead Research AI Agent",
        "tagline": "An AI employee that finds qualified sales leads, researches fit, and returns an actionable prospect list.",
        "description": (
            "Use this Gobii AI employee to find B2B companies that match an ideal customer profile, evaluate "
            "account fit, identify useful buying signals, and return a structured prospect list with source links "
            "and outreach angles."
        ),
        "seo_meta_description": (
            "Use Gobii's B2B lead research AI employee to find qualified sales leads, research company fit, "
            "and return a source-linked prospect list."
        ),
        "charter": (
            "Research B2B prospects that match the user's ideal customer profile. Confirm target industries, "
            "geography, company size, buyer personas, qualification signals, and exclusions. Return a structured "
            "prospect list with sources, fit notes, gaps, buying signals, and suggested outreach angles."
        ),
        "best_for": (
            "- Sales teams building targeted prospect lists\n"
            "- Founders doing outbound sales or customer discovery\n"
            "- Agencies researching leads for client campaigns\n"
            "- RevOps and growth teams testing new markets"
        ),
        "example_outputs": (
            "- Source-linked prospect lists\n"
            "- ICP fit summaries and gaps\n"
            "- Buying-signal notes\n"
            "- Suggested buyer personas and outreach angles"
        ),
        "required_inputs": (
            "- Product and ideal customer profile\n"
            "- Target industries, geographies, and company sizes\n"
            "- Buyer personas and qualification signals\n"
            "- Explicit exclusions"
        ),
        "how_it_works": (
            "1. Reviews the ICP and qualification rules.\n"
            "2. Searches public, business-relevant sources.\n"
            "3. Evaluates each account against the criteria.\n"
            "4. Returns a structured list with sources and fit reasoning."
        ),
        "category": "Sales",
        "recommended_contact_channel": "email",
        "priority": 52,
    },
    {
        "code": "account-research-ai-agent",
        "slug": "account-research-agent",
        "display_name": "Account Research AI Agent",
        "tagline": "An AI employee that researches target accounts, summarizes fit, and finds useful outreach angles.",
        "description": (
            "Use this Gobii AI employee to research target companies before sales outreach. It reviews public "
            "business sources, summarizes company fit, identifies useful buying signals, and returns sales-ready "
            "account briefs with sources and outreach angles."
        ),
        "seo_meta_description": (
            "Use Gobii's account research AI employee to research target companies, summarize fit, identify "
            "buying signals, and return source-linked account briefs."
        ),
        "charter": (
            "Research the user's target accounts before outreach. For each account, summarize the company, "
            "business model, relevant news and signals, likely pain points, possible buyer personas, fit against "
            "the user's ICP, source links, gaps, and personalized outreach angles."
        ),
        "best_for": (
            "- Sales reps preparing for outbound outreach\n"
            "- Founders researching target customers\n"
            "- Agencies building account briefs\n"
            "- RevOps teams enriching account lists"
        ),
        "example_outputs": (
            "- Sales-ready account briefs\n"
            "- Company and business-model summaries\n"
            "- Buying signals and fit notes\n"
            "- Personalized outreach angles"
        ),
        "required_inputs": (
            "- Company name, website, or account list\n"
            "- ICP and product context\n"
            "- Buyer personas and qualification criteria\n"
            "- Research depth and output format"
        ),
        "how_it_works": (
            "1. Reviews the account and qualification criteria.\n"
            "2. Searches public, business-relevant sources.\n"
            "3. Evaluates company fit and recent signals.\n"
            "4. Returns a source-linked account brief."
        ),
        "category": "Sales",
        "recommended_contact_channel": "email",
        "priority": 54,
    },
    {
        "code": "ai-agent-for-candidate-sourcing",
        "slug": "candidate-sourcing-agent",
        "display_name": "Candidate Sourcing AI Employee",
        "tagline": (
            "Find and qualify candidates, prepare personalized outreach, and keep "
            "the recruiting funnel measurable."
        ),
        "description": (
            "Use this Gobii AI employee to discover and qualify candidate prospects "
            "across public, professional, role-relevant sources. It evaluates fit "
            "against the hiring criteria and returns a source-linked shortlist with "
            "evidence, gaps, and recruiter review notes. It can also draft personalized "
            "outreach for human approval, track candidate and response status, and "
            "prepare recruiting-funnel metrics and weekly progress reports."
        ),
        "seo_meta_description": (
            "Use Gobii's candidate sourcing AI employee to find qualified candidates, "
            "draft outreach, track responses, and report on the recruiting funnel."
        ),
        "charter": (
            "Run a human-reviewed candidate-sourcing workflow for the user's open role. "
            "Confirm required and preferred skills, seniority, location, target industries, "
            "company backgrounds, and exclusions. Discover and qualify prospects using "
            "role-relevant public evidence. Return a structured shortlist with sources, "
            "fit reasoning, and gaps. For approved candidates, draft personalized outreach "
            "without sending autonomously, maintain candidate and response status, and "
            "summarize funnel progress, response rates, blockers, and next actions."
        ),
        "best_for": (
            "- Recruiters building and qualifying candidate pipelines\n"
            "- Founders and hiring managers sourcing directly\n"
            "- Recruiting agencies managing reviewed outreach\n"
            "- Teams tracking candidate status and response rates\n"
            "- Recruiting leaders preparing weekly funnel reports"
        ),
        "example_outputs": (
            "- Recruiter-ready candidate shortlists\n"
            "- Fit scores and match summaries\n"
            "- Source links and evidence\n"
            "- Personalized outreach drafts for human approval\n"
            "- Candidate, outreach, and response trackers\n"
            "- Weekly funnel metrics, response rates, and next-step reports"
        ),
        "required_inputs": (
            "- Role description\n"
            "- Required and preferred skills\n"
            "- Seniority and location requirements\n"
            "- Target industries and exclusions\n"
            "- Outreach voice, channel, and approval rules\n"
            "- Candidate stages and reporting cadence"
        ),
        "how_it_works": (
            "1. Extracts the sourcing, qualification, and exclusion criteria.\n"
            "2. Searches role-relevant public and professional sources.\n"
            "3. Evaluates each prospect and returns a source-linked shortlist.\n"
            "4. Drafts personalized outreach for candidates approved by a recruiter.\n"
            "5. Updates candidate, outreach, and response status in the chosen tracker.\n"
            "6. Reports funnel metrics, response rates, blockers, and next actions."
        ),
        "customization_notes": (
            "Choose the candidate stages, qualification rubric, outreach voice, required "
            "human approvals, reporting cadence, and privacy constraints. Greenhouse can "
            "create approved candidate records, Google Sheets can maintain the shared "
            "candidate and response tracker, and Slack can deliver shortlist alerts, "
            "notifications, and weekly recruiting-funnel reports."
        ),
        "expected_tools_summary": (
            "- **Greenhouse:** Create approved candidate records and maintain ATS handoff.\n"
            "- **Google Sheets:** Track candidates, outreach status, responses, and funnel metrics.\n"
            "- **Slack:** Send shortlist alerts, status notifications, and weekly recruiting reports."
        ),
        "default_tools": [
            "greenhouse-create-candidate",
            "google_sheets-add-single-row",
            "slack-post-message",
        ],
        "category": "Recruiting",
        "recommended_contact_channel": "email",
        "priority": 60,
    },
    {
        "code": "candidate-researcher",
        "slug": "candidate-researcher",
        "display_name": "Candidate Researcher",
        "tagline": "An AI employee that enriches candidate profiles with work history, public evidence, and interview context.",
        "description": (
            "Use Candidate Researcher after sourcing to build a deeper, source-linked profile of each prospect. "
            "The AI employee reviews public professional history, career progression, projects, publications, "
            "and other role-relevant evidence, then returns concise context for recruiter and hiring-manager review."
        ),
        "seo_meta_description": (
            "Use Gobii's Candidate Researcher AI employee to enrich candidate profiles with source-linked work "
            "history, public evidence, gaps, and interview context."
        ),
        "charter": (
            "Research candidates supplied by the user. Focus on the role-relevant dimensions they identify, such "
            "as career progression, technical work, leadership, publications, projects, or industry experience. "
            "Return concise, source-linked profiles that separate verified facts, reasonable inferences, gaps, "
            "and suggested interview questions. Do not make protected-class inferences or hiring decisions."
        ),
        "best_for": (
            "- Recruiters preparing candidate profiles\n"
            "- Hiring managers reviewing a shortlist\n"
            "- Search teams researching hard-to-fill roles\n"
            "- Interview teams preparing evidence-based questions"
        ),
        "example_outputs": (
            "- Source-linked candidate profiles\n"
            "- Career-progression summaries\n"
            "- Relevant project and publication evidence\n"
            "- Gaps and suggested interview questions"
        ),
        "required_inputs": (
            "- Candidate names and source profiles\n"
            "- Role description and evaluation dimensions\n"
            "- Desired research depth\n"
            "- Explicit compliance and privacy constraints"
        ),
        "how_it_works": (
            "1. Reviews the supplied candidate and role criteria.\n"
            "2. Researches role-relevant public professional evidence.\n"
            "3. Separates sourced facts, inferences, and unknowns.\n"
            "4. Returns a concise profile for human review."
        ),
        "customization_notes": (
            "Choose the dimensions that matter for the role and explicitly exclude protected characteristics, "
            "personal data that is not job-relevant, and automated hiring decisions."
        ),
        "category": "Recruiting",
        "recommended_contact_channel": "email",
        "priority": 65,
    },
    {
        "code": "outreach-agent",
        "slug": "outreach-agent",
        "display_name": "Outreach Agent",
        "tagline": "An AI employee that drafts personalized outreach and organizes human-approved follow-ups.",
        "description": (
            "Use Outreach Agent when research is complete and a person needs tailored first-touch and follow-up "
            "drafts. It turns approved prospect or candidate context into concise messages, matches the team's "
            "voice, and maintains a review-ready outreach plan without making autonomous send decisions."
        ),
        "seo_meta_description": (
            "Use Gobii's Outreach Agent AI employee to draft personalized prospect or candidate outreach and "
            "organize human-approved follow-ups."
        ),
        "charter": (
            "Draft personalized outreach from user-approved prospect or candidate context. Learn the user's voice, "
            "channel, audience, value proposition, and compliance constraints. Produce first-touch and follow-up "
            "drafts with the source context used for personalization. Keep a review-ready status plan, but do not "
            "send messages or make contact without explicit human approval."
        ),
        "best_for": (
            "- Sales teams preparing personalized prospecting drafts\n"
            "- Recruiters preparing candidate outreach\n"
            "- Founders running small, reviewed outreach programs\n"
            "- Teams that need consistent follow-up preparation"
        ),
        "example_outputs": (
            "- Personalized first-touch drafts\n"
            "- Human-reviewed follow-up sequences\n"
            "- Personalization notes tied to approved research\n"
            "- Outreach status and review queues"
        ),
        "required_inputs": (
            "- Approved prospect or candidate research\n"
            "- Audience, offer, and desired action\n"
            "- Brand voice and channel constraints\n"
            "- Compliance rules and approval process"
        ),
        "how_it_works": (
            "1. Reviews approved research and messaging constraints.\n"
            "2. Selects relevant, source-backed personalization.\n"
            "3. Drafts first-touch and follow-up messages.\n"
            "4. Returns the drafts and status plan for human approval."
        ),
        "customization_notes": (
            "Define the audience, channel, tone, allowed claims, follow-up cadence, and mandatory approval step. "
            "Connect downstream sending tools only when the organization has an explicit review process."
        ),
        "category": "Sales",
        "recommended_contact_channel": "email",
        "priority": 70,
    },
)


def ensure_canonical_templates(apps, schema_editor):
    Template = apps.get_model("api", "PersistentAgentTemplate")

    Template.objects.filter(code="talent-sourcer").update(
        is_active=False,
        show_on_homepage=False,
    )

    for code, category in EXACT_LEGACY_TEMPLATE_CATEGORIES.items():
        Template.objects.filter(code=code).update(
            slug=code,
            category=category,
            organization_id=None,
            public_profile_id=None,
            is_official=True,
            is_active=True,
        )

    for template_data in TEMPLATES:
        code = template_data["code"]
        defaults = {
            **template_data,
            "organization_id": None,
            "public_profile_id": None,
            "is_official": True,
            "is_active": True,
            "show_on_homepage": False,
        }
        template, created = Template.objects.get_or_create(code=code, defaults=defaults)
        if created:
            continue

        update_fields = []
        for field_name in (
            "slug",
            "category",
            "is_official",
            "is_active",
        ):
            if getattr(template, field_name) != defaults[field_name]:
                setattr(template, field_name, defaults[field_name])
                update_fields.append(field_name)
        if template.organization_id is not None:
            template.organization_id = None
            update_fields.append("organization")
        if template.public_profile_id is not None:
            template.public_profile_id = None
            update_fields.append("public_profile")
        forced_fields = set(FORCED_CANONICAL_TEMPLATE_FIELDS.get(code, ()))
        for field_name in (
            "display_name",
            "tagline",
            "description",
            "seo_meta_description",
            "charter",
            "description_markdown",
            "best_for",
            "example_outputs",
            "required_inputs",
            "how_it_works",
            "customization_notes",
            "expected_tools_summary",
            "default_tools",
        ):
            current_value = getattr(template, field_name, "")
            default_value = defaults.get(field_name)
            if (
                default_value
                and current_value != default_value
                and (field_name in forced_fields or not current_value)
            ):
                setattr(template, field_name, defaults[field_name])
                update_fields.append(field_name)
        if update_fields:
            update_fields.append("updated_at")
            template.save(update_fields=update_fields)

    for code, merged_notes in MERGED_CUSTOMIZATION_NOTES.items():
        template = Template.objects.filter(code=code).first()
        if not template:
            continue
        current_notes = str(template.customization_notes or "").strip()
        if merged_notes in current_notes:
            continue
        template.customization_notes = "\n\n".join(
            part for part in (current_notes, merged_notes) if part
        )
        template.save(update_fields=["customization_notes", "updated_at"])

    capital_raise_primary = Template.objects.filter(
        code=CAPITAL_RAISE_PRIMARY_CODE
    ).first()
    capital_raise_duplicate = Template.objects.filter(
        code=CAPITAL_RAISE_DUPLICATE_CODE
    ).first()
    same_community_owner = (
        capital_raise_primary
        and capital_raise_duplicate
        and capital_raise_primary.public_profile_id
        and capital_raise_primary.public_profile_id
        == capital_raise_duplicate.public_profile_id
        and capital_raise_primary.created_by_id
        == capital_raise_duplicate.created_by_id
    )
    if capital_raise_duplicate and not same_community_owner:
        raise RuntimeError(
            "Cannot consolidate the capital-raise duplicate without a matching "
            "canonical template owned by the same community creator."
        )
    if same_community_owner:
        for field_name, value in CAPITAL_RAISE_PRIMARY_CONTENT.items():
            setattr(capital_raise_primary, field_name, value)
        capital_raise_primary.save(
            update_fields=[*CAPITAL_RAISE_PRIMARY_CONTENT, "updated_at"]
        )
        capital_raise_duplicate.is_active = False
        capital_raise_duplicate.show_on_homepage = False
        capital_raise_duplicate.save(
            update_fields=["is_active", "show_on_homepage"]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0441_disable_unseen_web_chat_followups"),
    ]

    operations = [
        migrations.RunPython(
            ensure_canonical_templates,
            migrations.RunPython.noop,
        ),
    ]
