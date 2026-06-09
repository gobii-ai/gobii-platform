COMPARISON_STATUS_COMING_SOON = "coming_soon"
COMPARISON_STATUS_PUBLISHED = "published"

COMPARISON_CATALOG = (
    {
        "slug": "openclaw-vs-gobii",
        "competitor_name": "OpenClaw",
        "title": "OpenClaw vs Gobii",
        "template_name": "comparisons/detail.html",
        "seo_title": "OpenClaw vs Gobii: AI Agent Platform Comparison | Gobii",
        "seo_description": (
            "Comparing OpenClaw vs Gobii? See how Gobii and OpenClaw differ across "
            "always-on execution, browser automation, security, memory, and deployment model."
        ),
        "summary": (
            "A formal comparison for teams evaluating always-on AI agents, production "
            "browser automation, structured state, security posture, and deployment model."
        ),
        "status": COMPARISON_STATUS_PUBLISHED,
        "target_keywords": (
            "OpenClaw vs Gobii",
            "Gobii vs OpenClaw",
            "OpenClaw alternative",
            "AI agent platform comparison",
        ),
        "competitor_url": "https://github.com/openclaw/openclaw",
        "published_date": "2026-06-03",
        "last_reviewed_date": "2026-06-03",
        "last_reviewed_display": "June 3, 2026",
        "reviewed_by": "Gobii editorial team",
        "source_reviewed": "June 2026",
    },
    {
        "slug": "n8n-vs-gobii",
        "competitor_name": "n8n",
        "title": "n8n vs Gobii",
        "template_name": "comparisons/detail_n8n.html",
        "seo_title": "n8n vs Gobii: Workflow Automation or AI Coworkers? | Gobii",
        "seo_description": (
            "Comparing n8n vs Gobii? See when to choose n8n's technical workflow "
            "automation canvas and when to choose Gobii's persistent browser-native AI coworkers."
        ),
        "summary": (
            "A professional comparison for teams deciding between technical workflow "
            "automation in n8n and persistent Gobii agents for browser-native business work."
        ),
        "status": COMPARISON_STATUS_PUBLISHED,
        "target_keywords": (
            "n8n vs Gobii",
            "Gobii vs n8n",
            "n8n alternative",
            "AI workflow automation comparison",
        ),
        "competitor_url": "https://n8n.io/",
        "competitor_application_category": "Workflow automation platform",
        "competitor_schema_description": (
            "Workflow automation platform for apps, APIs, integrations, and technical automation."
        ),
        "published_date": "2026-06-04",
        "last_reviewed_date": "2026-06-04",
        "last_reviewed_display": "June 4, 2026",
        "reviewed_by": "Gobii editorial team",
        "source_reviewed": "June 2026",
    },
    {
        "slug": "zapier-agents-vs-gobii",
        "competitor_name": "Zapier Agents",
        "title": "Zapier Agents vs Gobii",
        "template_name": "comparisons/detail_zapier_agents.html",
        "seo_title": "Zapier Agents vs Gobii: AI Coworker Comparison | Gobii",
        "seo_description": (
            "Comparing Zapier Agents vs Gobii? See when to choose Zapier's connected-app "
            "AI agents and when to choose Gobii's persistent browser-native AI coworkers."
        ),
        "summary": (
            "A practical comparison for teams deciding between Zapier Agents for connected-app "
            "automation and Gobii for persistent browser-native AI coworker workflows."
        ),
        "status": COMPARISON_STATUS_PUBLISHED,
        "target_keywords": (
            "Zapier Agents vs Gobii",
            "Gobii vs Zapier Agents",
            "Zapier Agents alternative",
            "AI agent automation comparison",
        ),
        "competitor_url": "https://zapier.com/agents",
        "competitor_application_category": "AI agent automation platform",
        "competitor_operating_system": "Web",
        "competitor_same_as": (
            "https://zapier.com/agents",
        ),
        "competitor_schema_description": (
            "AI agents for automating work across Zapier's connected app ecosystem."
        ),
        "published_date": "2026-06-07",
        "last_reviewed_date": "2026-06-07",
        "last_reviewed_display": "June 7, 2026",
        "reviewed_by": "Gobii editorial team",
        "source_reviewed": "June 2026",
    },
)


def get_comparison(slug: str) -> dict | None:
    normalized_slug = str(slug or "").strip()
    for comparison in COMPARISON_CATALOG:
        if comparison["slug"] == normalized_slug:
            return comparison
    return None


def get_published_comparisons() -> tuple[dict, ...]:
    return tuple(
        comparison
        for comparison in COMPARISON_CATALOG
        if comparison["status"] == COMPARISON_STATUS_PUBLISHED
    )
