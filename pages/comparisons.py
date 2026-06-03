COMPARISON_STATUS_COMING_SOON = "coming_soon"
COMPARISON_STATUS_PUBLISHED = "published"

COMPARISON_CATALOG = (
    {
        "slug": "gobii-vs-openclaw",
        "competitor_name": "OpenClaw",
        "title": "Gobii vs OpenClaw",
        "seo_title": "Gobii vs OpenClaw: AI Agent Platform Comparison",
        "seo_description": (
            "Compare Gobii and OpenClaw across always-on execution, browser automation, "
            "agent identity, security, memory, and deployment model. See why Gobii is the "
            "stronger fit for production team workflows."
        ),
        "summary": (
            "A formal comparison for teams evaluating always-on AI agents, production "
            "browser automation, structured state, security posture, and deployment model."
        ),
        "status": COMPARISON_STATUS_PUBLISHED,
        "target_keywords": (
            "Gobii vs OpenClaw",
            "OpenClaw alternative",
            "AI agent platform comparison",
        ),
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
