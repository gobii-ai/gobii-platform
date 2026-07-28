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
        "The former Talent Scout workflow supported real-time alerts, daily batches, or weekly shortlists and "
        "could pivot into adjacent talent pools as recruiters refined role, location, seniority, and experience "
        "criteria."
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
        "display_name": "Candidate Sourcing AI Agent",
        "tagline": "An AI employee that finds qualified candidate prospects, evaluates fit, and returns a recruiter-ready shortlist.",
        "description": (
            "Use this Gobii AI employee for candidate sourcing across public, professional, role-relevant sources. "
            "It evaluates prospects against hiring criteria and returns a recruiter-ready shortlist with source "
            "links, fit notes, gaps, and suggested outreach angles."
        ),
        "seo_meta_description": (
            "Use Gobii's candidate sourcing AI employee to find qualified candidates, evaluate fit, and return "
            "a recruiter-ready shortlist with sources."
        ),
        "charter": (
            "Source candidate prospects for the user's open role. Confirm required and preferred skills, "
            "seniority, location, target industries, company backgrounds, and exclusions. Return a structured "
            "shortlist with source links, fit reasoning, gaps, and suggested recruiter follow-up."
        ),
        "best_for": (
            "- Recruiters building candidate lists\n"
            "- Founders and hiring managers sourcing directly\n"
            "- Recruiting agencies researching prospects\n"
            "- Teams that need a structured shortlist"
        ),
        "example_outputs": (
            "- Recruiter-ready candidate shortlists\n"
            "- Fit scores and match summaries\n"
            "- Source links and evidence\n"
            "- Gaps and suggested outreach angles"
        ),
        "required_inputs": (
            "- Role description\n"
            "- Required and preferred skills\n"
            "- Seniority and location requirements\n"
            "- Target industries and exclusions"
        ),
        "how_it_works": (
            "1. Extracts the sourcing criteria.\n"
            "2. Searches role-relevant public sources.\n"
            "3. Evaluates each prospect against the criteria.\n"
            "4. Returns a source-linked shortlist for recruiter review."
        ),
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
        for field_name in (
            "seo_meta_description",
            "description_markdown",
            "best_for",
            "example_outputs",
            "required_inputs",
            "how_it_works",
            "customization_notes",
        ):
            if not getattr(template, field_name, "") and defaults.get(field_name):
                setattr(template, field_name, defaults[field_name])
                update_fields.append(field_name)
        if update_fields:
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
        template.save(update_fields=["customization_notes"])


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
