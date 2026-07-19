AGENT_API_LAST_MODIFIED_DATE = "2026-07-18"

AGENT_API_DEVELOPER_DOCS_URL = "https://docs.gobii.ai/developers"
AGENT_API_DOCS_URL = "https://docs.gobii.ai/developers/developer-agents"

AGENT_API_CLUSTER_GROUPS = (
    {
        "title": "Understand the category",
        "description": "Definitions and practical distinctions for teams evaluating agentic systems.",
        "links": (
            {
                "anchor": "What is an agentic API?",
                "url": "/blog/what-is-an-agentic-api/",
                "status": "live",
            },
            {
                "anchor": "Agentic API vs AI API",
                "url": "/blog/agentic-api-vs-ai-api/",
                "status": "live",
            },
            {
                "anchor": "Autonomous agent API",
                "url": "/blog/autonomous-agent-api/",
                "status": "planned",
            },
        ),
    },
    {
        "title": "Build a delegated workflow",
        "description": "Documentation, examples, and implementation guides for developers.",
        "links": (
            {
                "anchor": "Developer documentation",
                "url": AGENT_API_DEVELOPER_DOCS_URL,
                "status": "live",
            },
            {
                "anchor": "Agent API documentation and quickstart",
                "url": AGENT_API_DOCS_URL,
                "status": "live",
            },
            {
                "anchor": "Agentic API examples",
                "url": "/blog/agentic-api-examples/",
                "status": "planned",
            },
            {
                "anchor": "Agentic API tutorial",
                "url": "/blog/agentic-api-tutorial/",
                "status": "planned",
            },
            {
                "anchor": "AI agent API in Python",
                "url": "/blog/ai-agent-api-python/",
                "status": "planned",
            },
        ),
    },
    {
        "title": "Connect tools and orchestration",
        "description": "How agents reach tools, browsers, data, and action surfaces.",
        "links": (
            {
                "anchor": "APIs for AI agents",
                "url": "/blog/apis-for-ai-agents/",
                "status": "planned",
            },
            {
                "anchor": "Agent orchestration API",
                "url": "/blog/agent-orchestration-api/",
                "status": "planned",
            },
            {
                "anchor": "MCP agent API",
                "url": "/blog/mcp-agent-api/",
                "status": "planned",
            },
        ),
    },
    {
        "title": "Explore execution APIs",
        "description": "Focused paths for browser work, web automation, and computer use.",
        "links": (
            {
                "anchor": "Web automation API",
                "url": "/web-automation-api/",
                "status": "planned",
            },
            {
                "anchor": "Browser agent API",
                "url": "/browser-agent-api/",
                "status": "planned",
            },
            {
                "anchor": "Computer use API",
                "url": "/blog/computer-use-api/",
                "status": "planned",
            },
        ),
    },
)

AGENT_API_FAQ_ITEMS = (
    {
        "question": "What is an agentic API?",
        "answer": (
            "An agentic API lets software delegate a goal to an AI agent that can work through multiple "
            "steps, use approved tools, preserve relevant context, and report progress or results. A normal "
            "AI API commonly returns a model response to one request. An agentic API adds the runtime and "
            "control surface needed to carry work across a workflow."
        ),
    },
    {
        "question": "What does Gobii's Agent API manage?",
        "answer": (
            "Gobii's Agent API can create, retrieve, update, activate, deactivate, and delete persistent "
            "agents. Developers can set a charter and schedule, send inbound messages, inspect a timeline "
            "and processing state, and retrieve recent browser tasks associated with an agent."
        ),
    },
    {
        "question": "How is an Agent API different from a normal AI API?",
        "answer": (
            "A normal AI API is usually centered on generating a response from supplied input. An Agent API "
            "is centered on managing work over time: the agent has instructions and lifecycle state, can be "
            "triggered again, may use configured tools, and exposes activity that an application can inspect."
        ),
    },
    {
        "question": "What work can teams delegate through Gobii's Agent API?",
        "answer": (
            "Good starting points include browser research, recurring monitoring, data collection, "
            "enrichment, first-pass QA, and structured handoffs. The strongest workflows have clear inputs, "
            "approved sources or tools, an expected output, and a person responsible for reviewing judgment "
            "calls."
        ),
    },
    {
        "question": "Is the Agent API the same as a browser automation API?",
        "answer": (
            "No. Browser automation is one execution capability an agent can use when work lives on the web. "
            "Gobii's Agent API manages a broader persistent agent resource, including its charter, schedule, "
            "messages, lifecycle, processing state, timeline, and configured tools."
        ),
    },
    {
        "question": "How does human supervision work with an autonomous agent API?",
        "answer": (
            "Autonomous does not need to mean unsupervised. Teams can narrow an agent's charter, schedule, "
            "tools, and contact policy; write approval or escalation rules into the workflow; inspect its "
            "timeline and processing state; and deactivate the agent without deleting its configuration."
        ),
    },
    {
        "question": "How do I start using the Gobii Agent API?",
        "answer": (
            "Create a Gobii account, generate an API key, and send it in the X-Api-Key header. A POST request "
            "to /api/v1/agents/ with a name, charter, and optional schedule creates the persistent agent. Use "
            "the Agent API documentation for current fields, endpoints, and examples."
        ),
    },
)

AGENT_API_WORKFLOW_ITEMS = (
    {
        "name": "Browser research",
        "description": (
            "Inspect approved web sources, gather relevant evidence, compare findings, and return links for "
            "review."
        ),
    },
    {
        "name": "Data collection and enrichment",
        "description": (
            "Fill defined fields from available sources, flag missing or uncertain values, and prepare a "
            "structured handoff."
        ),
    },
    {
        "name": "Recurring monitoring",
        "description": (
            "Wake on a schedule to check known sources, identify meaningful changes, and summarize what needs "
            "attention."
        ),
    },
    {
        "name": "First-pass QA",
        "description": (
            "Compare pages, records, or outputs against explicit criteria and route exceptions to a human "
            "owner."
        ),
    },
    {
        "name": "Event-driven operations",
        "description": (
            "Start a defined workflow from an inbound message or event, then expose activity through the "
            "agent timeline."
        ),
    },
    {
        "name": "Review-ready handoffs",
        "description": (
            "Package source-linked findings, files, or structured data so a person or downstream system can "
            "take the next step."
        ),
    },
)


def build_agent_api_structured_data(
    *,
    page_title,
    seo_title,
    seo_description,
    canonical_url,
    home_url,
    docs_url,
    engineering_url,
    terms_url,
    social_image_url,
    organization_logo_url,
    organization_same_as,
):
    organization_id = f"{home_url.rstrip('/')}#organization"
    website_id = f"{home_url.rstrip('/')}#website"
    page_id_root = canonical_url.rstrip("/")
    webpage_id = f"{page_id_root}#webpage"
    api_id = f"{page_id_root}#api"
    faq_id = f"{page_id_root}#faq"
    workflow_list_id = f"{page_id_root}#workflows"
    breadcrumb_id = f"{page_id_root}#breadcrumb"

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
    web_api_schema = {
        "@type": "WebAPI",
        "@id": api_id,
        "name": "Gobii Agent API",
        "serviceType": "Agentic AI API",
        "url": canonical_url,
        "image": social_image_url,
        "description": seo_description,
        "documentation": docs_url,
        "provider": {"@id": organization_id},
        "termsOfService": terms_url,
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
            for item in AGENT_API_FAQ_ITEMS
        ],
    }
    workflow_schema = {
        "@type": "ItemList",
        "@id": workflow_list_id,
        "name": "Agent API workflow examples",
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
            for index, item in enumerate(AGENT_API_WORKFLOW_ITEMS, start=1)
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
        "about": {"@id": api_id},
        "mainEntity": {"@id": api_id},
        "mainEntityOfPage": {"@id": webpage_id},
        "dateModified": AGENT_API_LAST_MODIFIED_DATE,
        "breadcrumb": {"@id": breadcrumb_id},
        "hasPart": [
            {"@id": faq_id},
            {"@id": workflow_list_id},
        ],
        "significantLink": [
            docs_url,
            engineering_url,
        ],
    }
    return {
        "@context": "https://schema.org",
        "@graph": [
            organization_schema,
            website_schema,
            webpage_schema,
            web_api_schema,
            faq_schema,
            breadcrumb_schema,
            workflow_schema,
        ],
    }
