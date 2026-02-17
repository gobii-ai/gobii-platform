import json
import uuid
from collections import Counter
from json import JSONDecodeError
from typing import Any

from django.core.cache import cache
from django.db.models import Count
from django.http import HttpRequest, JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView, View

from api.models import PersistentAgentTemplate, PersistentAgentTemplateLike

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


def _build_library_payload() -> dict[str, Any]:
    templates = list(
        PersistentAgentTemplate.objects.select_related("public_profile")
        .filter(public_profile__isnull=False, is_active=True)
        .exclude(slug="")
        .order_by("priority", "display_name")
    )

    category_counts: Counter[str] = Counter()
    agents: list[dict[str, Any]] = []

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
                "_priority": template.priority,
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
    return {
        "agents": agents,
        "topCategories": top_categories,
    }


def _get_library_payload() -> dict[str, Any]:
    cached = cache.get(LIBRARY_CACHE_KEY)
    if isinstance(cached, dict):
        cached_agents = cached.get("agents")
        cached_categories = cached.get("topCategories")
        if isinstance(cached_agents, list) and isinstance(cached_categories, list):
            return cached

    payload = _build_library_payload()
    cache.set(LIBRARY_CACHE_KEY, payload, timeout=LIBRARY_CACHE_TTL_SECONDS)
    return payload


def _attach_like_state(
    agents: list[dict[str, Any]],
    *,
    viewer_user_id: int | None,
) -> tuple[list[dict[str, Any]], int]:
    if not agents:
        return [], 0

    template_ids = [agent["id"] for agent in agents]
    like_rows = (
        PersistentAgentTemplateLike.objects
        .filter(template_id__in=template_ids)
        .values("template_id")
        .annotate(count=Count("id"))
    )
    like_counts_by_template_id = {
        str(row["template_id"]): int(row["count"])
        for row in like_rows
    }

    liked_template_ids: set[str] = set()
    if viewer_user_id is not None:
        liked_template_ids = {
            str(template_id)
            for template_id in PersistentAgentTemplateLike.objects.filter(
                template_id__in=template_ids,
                user_id=viewer_user_id,
            ).values_list("template_id", flat=True)
        }

    total_likes = 0
    enriched_agents: list[dict[str, Any]] = []
    for agent in agents:
        agent_id = agent["id"]
        like_count = like_counts_by_template_id.get(agent_id, 0)
        total_likes += like_count
        enriched_agents.append(
            {
                **agent,
                "likeCount": like_count,
                "isLiked": agent_id in liked_template_ids,
            }
        )

    return enriched_agents, total_likes


def _sort_agents_by_popularity(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        agents,
        key=lambda agent: (
            -int(agent.get("likeCount", 0)),
            int(agent.get("_priority", 100)),
            str(agent.get("name", "")).casefold(),
        ),
    )


def _matches_search_query(agent: dict[str, Any], normalized_query: str) -> bool:
    searchable_fields = (
        agent.get("name", ""),
        agent.get("tagline", ""),
        agent.get("description", ""),
        agent.get("category", ""),
        agent.get("publicProfileHandle", ""),
    )
    return any(normalized_query in str(field).casefold() for field in searchable_fields)


def _parse_json_payload(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


@method_decorator(ensure_csrf_cookie, name="dispatch")
class LibraryView(TemplateView):
    template_name = "library.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_name"] = "Agent Discovery"
        return context


class LibraryAgentsAPIView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        payload = _get_library_payload()
        viewer_user_id = request.user.id if request.user.is_authenticated else None

        category = _normalize_category(request.GET.get("category")) if request.GET.get("category") else ""
        search_query = str(request.GET.get("q") or "").strip()
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

        all_agents_with_likes, library_total_likes = _attach_like_state(
            payload["agents"],
            viewer_user_id=viewer_user_id,
        )
        ranked_agents = _sort_agents_by_popularity(all_agents_with_likes)

        if category:
            normalized_category = category.casefold()
            filtered_agents = [
                agent for agent in ranked_agents
                if agent["category"].casefold() == normalized_category
            ]
        else:
            filtered_agents = ranked_agents

        if search_query:
            normalized_query = search_query.casefold()
            filtered_agents = [
                agent for agent in filtered_agents
                if _matches_search_query(agent, normalized_query)
            ]

        total_agents = len(filtered_agents)
        page_agents = [
            {
                key: value
                for key, value in agent.items()
                if key != "_priority"
            }
            for agent in filtered_agents[offset:offset + limit]
        ]

        return JsonResponse(
            {
                "agents": page_agents,
                "topCategories": payload["topCategories"],
                "totalAgents": total_agents,
                "libraryTotalAgents": len(ranked_agents),
                "libraryTotalLikes": library_total_likes,
                "offset": offset,
                "limit": limit,
                "hasMore": (offset + limit) < total_agents,
            }
        )


class LibraryAgentLikeAPIView(View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required."}, status=401)

        payload = _parse_json_payload(request)
        agent_id = str(payload.get("agentId") or "").strip()
        if not agent_id:
            return JsonResponse({"error": "agentId is required."}, status=400)

        try:
            agent_uuid = uuid.UUID(agent_id)
        except (TypeError, ValueError, AttributeError):
            return JsonResponse({"error": "agentId must be a valid UUID."}, status=400)

        template = (
            PersistentAgentTemplate.objects
            .filter(
                id=agent_uuid,
                public_profile__isnull=False,
                is_active=True,
            )
            .exclude(slug="")
            .first()
        )
        if template is None:
            return JsonResponse({"error": "Shared agent not found."}, status=404)

        like, created = PersistentAgentTemplateLike.objects.get_or_create(
            template=template,
            user=request.user,
        )
        if created:
            is_liked = True
        else:
            like.delete()
            is_liked = False

        like_count = PersistentAgentTemplateLike.objects.filter(template=template).count()
        return JsonResponse(
            {
                "agentId": str(template.id),
                "isLiked": is_liked,
                "likeCount": like_count,
            }
        )
