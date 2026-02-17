import json
import uuid
from json import JSONDecodeError
from typing import Any

from django.core.cache import cache
from django.db.models import BooleanField, Case, CharField, Count, Exists, F, OuterRef, Q, Value, When
from django.db.models.functions import Lower
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


def _library_queryset():
    return (
        PersistentAgentTemplate.objects.select_related("public_profile")
        .filter(public_profile__isnull=False, is_active=True)
        .exclude(slug="")
    )


def _normalized_category_expression():
    return Case(
        When(Q(category__isnull=True) | Q(category=""), then=Value("Uncategorized")),
        default=F("category"),
        output_field=CharField(),
    )


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


def _build_top_categories() -> list[dict[str, Any]]:
    category_rows = (
        _library_queryset()
        .annotate(normalized_category=_normalized_category_expression())
        .values("normalized_category")
        .annotate(count=Count("id"))
        .order_by("-count", Lower("normalized_category"))[:10]
    )
    return [
        {"name": row["normalized_category"], "count": row["count"]}
        for row in category_rows
    ]


def _get_top_categories() -> list[dict[str, Any]]:
    cached = cache.get(LIBRARY_CACHE_KEY)
    if isinstance(cached, list):
        valid_items = all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("count"), int)
            for item in cached
        )
        if valid_items:
            return cached

    top_categories = _build_top_categories()
    cache.set(LIBRARY_CACHE_KEY, top_categories, timeout=LIBRARY_CACHE_TTL_SECONDS)
    return top_categories


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
        viewer_user_id = request.user.id if request.user.is_authenticated else None
        top_categories = _get_top_categories()

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

        library_queryset = _library_queryset().annotate(
            normalized_category=_normalized_category_expression(),
        )
        library_total_agents = library_queryset.count()
        library_total_likes = (
            PersistentAgentTemplateLike.objects.filter(
                template__public_profile__isnull=False,
                template__is_active=True,
            )
            .exclude(template__slug="")
            .count()
        )

        filtered_queryset = library_queryset
        if category:
            filtered_queryset = filtered_queryset.filter(
                normalized_category__iexact=category
            )

        if search_query:
            filtered_queryset = filtered_queryset.filter(
                Q(display_name__icontains=search_query)
                | Q(tagline__icontains=search_query)
                | Q(description__icontains=search_query)
                | Q(normalized_category__icontains=search_query)
                | Q(public_profile__handle__icontains=search_query)
            )

        total_agents = filtered_queryset.count()
        annotated_queryset = filtered_queryset.annotate(
            like_count=Count("template_likes"),
        )
        if viewer_user_id is not None:
            annotated_queryset = annotated_queryset.annotate(
                is_liked=Exists(
                    PersistentAgentTemplateLike.objects.filter(
                        template_id=OuterRef("pk"),
                        user_id=viewer_user_id,
                    ),
                )
            )
        else:
            annotated_queryset = annotated_queryset.annotate(
                is_liked=Value(False, output_field=BooleanField()),
            )

        page_templates = annotated_queryset.order_by(
            "-like_count",
            "priority",
            Lower("display_name"),
            "id",
        )[offset:offset + limit]

        page_agents = [
            {
                "id": str(template.id),
                "name": template.display_name,
                "tagline": template.tagline,
                "description": template.description,
                "category": template.normalized_category,
                "publicProfileHandle": template.public_profile.handle,
                "templateSlug": template.slug,
                "templateUrl": reverse(
                    "pages:public_template_detail",
                    kwargs={
                        "handle": template.public_profile.handle,
                        "template_slug": template.slug,
                    },
                ),
                "likeCount": template.like_count,
                "isLiked": template.is_liked,
            }
            for template in page_templates
        ]

        return JsonResponse(
            {
                "agents": page_agents,
                "topCategories": top_categories,
                "totalAgents": total_agents,
                "libraryTotalAgents": library_total_agents,
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
