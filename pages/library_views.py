from collections import Counter

from django.core.cache import cache
from django.http import JsonResponse
from django.urls import reverse
from django.views.generic import TemplateView, View

from api.models import PersistentAgentTemplate

LIBRARY_CACHE_KEY = "pages:library:payload:v1"
LIBRARY_CACHE_TTL_SECONDS = 120
LIBRARY_DEFAULT_PAGE_SIZE = 24
LIBRARY_MAX_PAGE_SIZE = 100


def _normalize_category(value: str | None) -> str:
    return (value or "").strip() or "Uncategorized"


def _parse_query_int(
    value: str | None,
    *,
    default: int,
    min_value: int,
    max_value: int | None = None,
) -> int:
    try:
        parsed = int(value) if value not in {None, ""} else default
    except (TypeError, ValueError):
        parsed = default
    parsed = max(parsed, min_value)
    if max_value is not None:
        parsed = min(parsed, max_value)
    return parsed


def _build_library_payload() -> dict[str, list[dict[str, str]]]:
    templates = list(
        PersistentAgentTemplate.objects.select_related("public_profile")
        .filter(public_profile__isnull=False, is_active=True)
        .exclude(slug="")
        .order_by("priority", "display_name")
    )

    category_counts: Counter[str] = Counter()
    agents: list[dict[str, str]] = []

    for template in templates:
        if template.public_profile is None:
            continue
        category = _normalize_category(template.category)
        category_counts[category] += 1
        agents.append(
            {
                "id": str(template.id),
                "name": template.display_name,
                "tagline": template.tagline,
                "description": template.description,
                "category": category,
                "publicProfileHandle": template.public_profile.handle,
                "templateSlug": template.slug,
                "templateUrl": reverse(
                    "pages:public_template_detail",
                    kwargs={
                        "handle": template.public_profile.handle,
                        "template_slug": template.slug,
                    },
                ),
            }
        )

    ranked_categories = sorted(
        category_counts.items(),
        key=lambda item: (-item[1], item[0].lower()),
    )
    top_categories = [
        {"name": name, "count": count}
        for name, count in ranked_categories[:10]
    ]
    return {"agents": agents, "topCategories": top_categories}


def _get_library_payload() -> dict[str, list[dict[str, str]]]:
    cached = cache.get(LIBRARY_CACHE_KEY)
    if isinstance(cached, dict):
        cached_agents = cached.get("agents")
        cached_categories = cached.get("topCategories")
        if isinstance(cached_agents, list) and isinstance(cached_categories, list):
            return cached

    payload = _build_library_payload()
    cache.set(LIBRARY_CACHE_KEY, payload, timeout=LIBRARY_CACHE_TTL_SECONDS)
    return payload


class LibraryView(TemplateView):
    template_name = "library.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_name"] = "Library"
        return context


class LibraryAgentsAPIView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        payload = _get_library_payload()
        category = _normalize_category(request.GET.get("category")) if request.GET.get("category") else ""
        limit = _parse_query_int(
            request.GET.get("limit"),
            default=LIBRARY_DEFAULT_PAGE_SIZE,
            min_value=1,
            max_value=LIBRARY_MAX_PAGE_SIZE,
        )
        offset = _parse_query_int(
            request.GET.get("offset"),
            default=0,
            min_value=0,
        )

        all_agents = payload["agents"]
        if category:
            normalized_category = category.casefold()
            filtered_agents = [
                agent for agent in all_agents
                if agent["category"].casefold() == normalized_category
            ]
        else:
            filtered_agents = all_agents

        total_agents = len(filtered_agents)
        page_agents = filtered_agents[offset:offset + limit]

        return JsonResponse(
            {
                "agents": page_agents,
                "topCategories": payload["topCategories"],
                "totalAgents": total_agents,
                "libraryTotalAgents": len(all_agents),
                "offset": offset,
                "limit": limit,
                "hasMore": (offset + limit) < total_agents,
            }
        )
