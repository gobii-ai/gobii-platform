COMPARISON_STATUS_COMING_SOON = "coming_soon"
COMPARISON_STATUS_PUBLISHED = "published"

COMPARISON_CATALOG = (
    {
        "slug": "openclaw-vs-gobii",
        "competitor_name": "OpenClaw",
        "title": "OpenClaw vs Gobii",
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
