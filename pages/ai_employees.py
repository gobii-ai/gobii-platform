AI_EMPLOYEES_CLUSTER_LINKS = (
    {
        "anchor": "best AI employees",
        "url": "/blog/best-ai-employees/",
        "status": "planned",
    },
    {
        "anchor": "hire AI employees",
        "url": "/blog/hire-ai-employees/",
        "status": "planned",
    },
    {
        "anchor": "AI employee app",
        "url": "/blog/ai-employee-app/",
        "status": "planned",
    },
    {
        "anchor": "AI employee company",
        "url": "/blog/ai-employee-company/",
        "status": "planned",
    },
    {
        "anchor": "what is an AI employee",
        "url": "/blog/what-is-an-ai-employee/",
        "status": "planned",
    },
    {
        "anchor": "AI workers",
        "url": "/blog/ai-workers/",
        "status": "planned",
    },
    {
        "anchor": "AI teammates",
        "url": "/blog/ai-teammates/",
        "status": "planned",
    },
    {
        "anchor": "AI employees vs AI agents",
        "url": "/blog/ai-employees-vs-ai-agents/",
        "status": "planned",
    },
    {
        "anchor": "AI agent examples",
        "url": "/blog/ai-agent-examples/",
        "status": "planned",
    },
    {
        "anchor": "AI agents for business",
        "url": "/blog/ai-agents-for-business/",
        "status": "planned",
    },
    {
        "anchor": "custom AI agents for business",
        "url": "/blog/custom-ai-agents-for-business/",
        "status": "planned",
    },
    {
        "anchor": "AI employees for business",
        "url": "/blog/ai-employees-for-business/",
        "status": "planned",
    },
    {
        "anchor": "AI sales agent",
        "url": "/solutions/sales/ai-sales-agent/",
        "route": "pages:solution_sales_ai_sales_agent",
        "status": "live",
    },
    {
        "anchor": "AI customer support agent",
        "url": "/solutions/customer-support/ai-customer-support-agent/",
        "status": "planned",
    },
    {
        "anchor": "AI recruiting agent",
        "url": "/solutions/recruiting/ai-recruiting-agent/",
        "status": "planned",
    },
    {
        "anchor": "AI marketing agent",
        "url": "/solutions/marketing/ai-marketing-agent/",
        "status": "planned",
    },
)

AI_EMPLOYEES_FAQ_ITEMS = (
    {
        "question": "What is an AI employee?",
        "answer": (
            "An AI employee is software that can own a defined workstream: gather context, use tools, "
            "take approved actions, and produce review-ready output. Gobii uses the warmer phrase AI "
            "teammate because the strongest workflows keep human judgment in charge while AI handles "
            "the repeatable execution."
        ),
    },
    {
        "question": "Can you have AI employees?",
        "answer": (
            "Yes. Teams can deploy AI employees when the work has clear inputs, approved tools, success "
            "criteria, and review points. The practical starting point is not a job title. It is one "
            "repeatable workflow that a virtual AI employee can run, document, and hand back to the team."
        ),
    },
    {
        "question": "How are AI employees different from chatbots?",
        "answer": (
            "Chatbots usually answer questions inside a conversation. AI employees do work across a "
            "workflow: they browse approved sources, update structured outputs, compare information, "
            "prepare drafts, and request review when judgment is needed. The difference is sustained "
            "execution, not just a better reply."
        ),
    },
    {
        "question": "What work can an AI employee do?",
        "answer": (
            "An AI employee can handle research, monitoring, enrichment, list building, first-pass "
            "analysis, document prep, CRM-ready updates, spreadsheet work, and handoff summaries. The "
            "best fit is high-context, repeatable work where sources, rules, and review criteria can "
            "be explained."
        ),
    },
    {
        "question": "How do you hire or deploy an AI employee?",
        "answer": (
            "To hire AI employees well, start with a workflow brief: the goal, input sources, allowed "
            "tools, output format, cadence, and human reviewer. Gobii AI teammates can then run the "
            "workflow, surface uncertainty, and improve as reviewers give feedback."
        ),
    },
)

AI_EMPLOYEES_WORKFLOW_ITEMS = (
    {
        "name": "Research and sourcing",
        "description": (
            "Find accounts, candidates, vendors, companies, or topics from approved sources and return "
            "source-linked notes."
        ),
    },
    {
        "name": "Qualification and enrichment",
        "description": (
            "Compare records against criteria, fill missing fields, flag uncertainty, and prepare "
            "review-ready tables."
        ),
    },
    {
        "name": "Monitoring and alerts",
        "description": (
            "Watch pages, feeds, directories, or public signals on a schedule and summarize meaningful "
            "changes."
        ),
    },
    {
        "name": "Drafting and preparation",
        "description": (
            "Turn gathered context into briefs, outreach drafts, status updates, summaries, and next-step "
            "recommendations."
        ),
    },
    {
        "name": "Handoff and operations",
        "description": (
            "Package outputs for humans or downstream tools with source links, review status, and clear "
            "decision points."
        ),
    },
)


def build_ai_employees_structured_data(
    *,
    page_title,
    seo_title,
    seo_description,
    canonical_url,
    home_url,
    pricing_url,
    social_image_url,
    organization_logo_url,
    organization_same_as,
    live_cluster_links,
):
    organization_id = f"{home_url.rstrip('/')}#organization"
    website_id = f"{home_url.rstrip('/')}#website"
    webpage_id = f"{canonical_url.rstrip('/')}#webpage"
    software_id = f"{canonical_url.rstrip('/')}#software"
    faq_id = f"{canonical_url.rstrip('/')}#faq"
    workflow_list_id = f"{canonical_url.rstrip('/')}#workflows"
    breadcrumb_id = f"{canonical_url.rstrip('/')}#breadcrumb"

    organization_schema = {
        "@type": "Organization",
        "@id": organization_id,
        "name": "Gobii",
        "url": home_url,
        "logo": organization_logo_url,
        "sameAs": list(organization_same_as),
    }
    website_schema = {
        "@type": "WebSite",
        "@id": website_id,
        "name": "Gobii",
        "url": home_url,
        "publisher": {"@id": organization_id},
    }
    software_schema = {
        "@type": "SoftwareApplication",
        "@id": software_id,
        "name": "Gobii AI Teammates",
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web",
        "url": canonical_url,
        "image": social_image_url,
        "description": seo_description,
        "publisher": {"@id": organization_id},
        "featureList": [
            "Browser-based workflow execution",
            "Human-reviewed AI employee handoffs",
            "Recurring research and monitoring workflows",
            "Structured output for spreadsheets, CRMs, and downstream tools",
        ],
        "offers": {
            "@type": "Offer",
            "url": pricing_url,
        },
    }
    faq_schema = {
        "@type": "FAQPage",
        "@id": faq_id,
        "mainEntity": [
            {
                "@type": "Question",
                "name": item["question"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": item["answer"],
                },
            }
            for item in AI_EMPLOYEES_FAQ_ITEMS
        ],
    }
    workflow_schema = {
        "@type": "ItemList",
        "@id": workflow_list_id,
        "name": "AI employee workflow examples",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "item": {
                    "@type": "Thing",
                    "name": item["name"],
                    "description": item["description"],
                },
            }
            for index, item in enumerate(AI_EMPLOYEES_WORKFLOW_ITEMS, start=1)
        ],
    }
    breadcrumb_schema = {
        "@type": "BreadcrumbList",
        "@id": breadcrumb_id,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "name": "Home",
                "item": home_url,
            },
            {
                "@type": "ListItem",
                "position": 2,
                "name": page_title,
                "item": canonical_url,
            },
        ],
    }
    webpage_schema = {
        "@type": "WebPage",
        "@id": webpage_id,
        "name": seo_title,
        "headline": page_title,
        "description": seo_description,
        "url": canonical_url,
        "image": social_image_url,
        "isPartOf": {"@id": website_id},
        "publisher": {"@id": organization_id},
        "about": {"@id": software_id},
        "mainEntity": {"@id": software_id},
        "mainEntityOfPage": {"@id": webpage_id},
        "dateModified": "2026-07-09",
        "breadcrumb": {"@id": breadcrumb_id},
        "hasPart": [
            {"@id": faq_id},
            {"@id": workflow_list_id},
        ],
        "significantLink": [link["absolute_url"] for link in live_cluster_links] + [pricing_url],
    }
    return {
        "@context": "https://schema.org",
        "@graph": [
            organization_schema,
            website_schema,
            webpage_schema,
            software_schema,
            faq_schema,
            breadcrumb_schema,
            workflow_schema,
        ],
    }
