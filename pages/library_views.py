import json
import uuid
from json import JSONDecodeError
from typing import Any

from django.core.cache import cache
from django.db.models import BooleanField, Case, CharField, Count, Exists, F, OuterRef, Q, Value, When
from django.db.models.functions import Lower
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView, View

from api.models import PersistentAgentTemplate, PersistentAgentTemplateLike, PersistentAgentTemplateUrlAlias
from pages.public_template_urls import (
    public_template_category_slug,
    public_template_category_slug_aliases_from_label,
    public_template_category_slug_from_label,
    public_template_detail_path,
)

LIBRARY_CACHE_KEY = "pages:library:payload:v1"
LIBRARY_OFFICIAL_CACHE_KEY = "pages:library:payload:official:v1"
LIBRARY_CATEGORY_SLUG_MAP_CACHE_KEY = "pages:library:category_slug_map:v1"
LIBRARY_CACHE_TTL_SECONDS = 120
LIBRARY_DEFAULT_PAGE_SIZE = 24
LIBRARY_MAX_PAGE_SIZE = 100


def _normalize_category(value: str | None) -> str:
    return (value or "").strip() or "Uncategorized"


def _library_queryset():
    return (
        PersistentAgentTemplate.objects.select_related("public_profile")
        .filter(public_profile__isnull=False, organization__isnull=True, is_active=True)
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


def _parse_query_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _build_top_categories(*, official_only: bool = False) -> list[dict[str, Any]]:
    queryset = _library_queryset()
    if official_only:
        queryset = queryset.filter(is_official=True)
    category_rows = (
        queryset.annotate(normalized_category=_normalized_category_expression())
        .values("normalized_category")
        .annotate(count=Count("id"))
        .order_by("-count", Lower("normalized_category"))[:10]
    )
    return [
        {"name": row["normalized_category"], "count": row["count"]}
        for row in category_rows
    ]


def _get_top_categories(*, official_only: bool = False) -> list[dict[str, Any]]:
    cache_key = LIBRARY_OFFICIAL_CACHE_KEY if official_only else LIBRARY_CACHE_KEY
    cached = cache.get(cache_key)
    if isinstance(cached, list):
        valid_items = all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("count"), int)
            for item in cached
        )
        if valid_items:
            return cached

    top_categories = _build_top_categories(official_only=official_only)
    cache.set(cache_key, top_categories, timeout=LIBRARY_CACHE_TTL_SECONDS)
    return top_categories


def _build_category_slug_map() -> dict[str, str]:
    category_rows = (
        _library_queryset()
        .annotate(normalized_category=_normalized_category_expression())
        .values_list("normalized_category", flat=True)
        .distinct()
    )
    category_slug_map = {}
    for category in category_rows:
        label = _normalize_category(category)
        category_slug_map[public_template_category_slug_from_label(label)] = label
        for alias_slug in public_template_category_slug_aliases_from_label(label):
            category_slug_map.setdefault(alias_slug, label)
    return category_slug_map


def _get_category_slug_map() -> dict[str, str]:
    cached = cache.get(LIBRARY_CATEGORY_SLUG_MAP_CACHE_KEY)
    if isinstance(cached, dict) and all(isinstance(key, str) and isinstance(value, str) for key, value in cached.items()):
        return cached

    category_slug_map = _build_category_slug_map()
    cache.set(LIBRARY_CATEGORY_SLUG_MAP_CACHE_KEY, category_slug_map, timeout=LIBRARY_CACHE_TTL_SECONDS)
    return category_slug_map


def _resolve_category_from_slug(category_slug: str | None) -> str:
    normalized_slug = str(category_slug or "").strip().lower()
    if not normalized_slug:
        return ""

    label = _get_category_slug_map().get(normalized_slug)
    if label:
        return label

    raise Http404("This library category is not available.")


def _get_legacy_library_handle_template(template_slug: str | None):
    template = _library_queryset().filter(
        public_profile__handle="library",
        slug=template_slug,
    ).first()
    if template:
        return template

    alias = (
        PersistentAgentTemplateUrlAlias.objects.select_related("template", "template__public_profile")
        .filter(
            public_profile__handle="library",
            slug=template_slug,
            template__is_active=True,
            template__public_profile__isnull=False,
            template__organization__isnull=True,
        )
        .first()
    )
    if alias:
        return alias.template
    return None


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


def _build_library_payload(
    request: HttpRequest,
    *,
    category: str = "",
    search_query: str = "",
    official_only: bool = False,
    limit: int = LIBRARY_DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    viewer_user_id = request.user.id if request.user.is_authenticated else None
    top_categories = _get_top_categories(official_only=official_only)

    normalized_category = _normalize_category(category) if category else ""
    normalized_search_query = str(search_query or "").strip()
    page_limit = max(1, min(limit, LIBRARY_MAX_PAGE_SIZE))
    page_offset = max(0, offset)

    library_queryset = _library_queryset().annotate(
        normalized_category=_normalized_category_expression(),
    )
    library_total_agents = library_queryset.count()
    official_total_agents = library_queryset.filter(is_official=True).count()
    library_total_likes = (
        PersistentAgentTemplateLike.objects.filter(
            template__public_profile__isnull=False,
            template__organization__isnull=True,
            template__is_active=True,
        )
        .exclude(template__slug="")
        .count()
    )
    official_total_likes = (
        PersistentAgentTemplateLike.objects.filter(
            template__public_profile__isnull=False,
            template__organization__isnull=True,
            template__is_active=True,
            template__is_official=True,
        )
        .exclude(template__slug="")
        .count()
    )

    filtered_queryset = library_queryset
    if official_only:
        filtered_queryset = filtered_queryset.filter(is_official=True)

    if normalized_category:
        filtered_queryset = filtered_queryset.filter(
            normalized_category__iexact=normalized_category
        )

    if normalized_search_query:
        filtered_queryset = filtered_queryset.filter(
            Q(display_name__icontains=normalized_search_query)
            | Q(tagline__icontains=normalized_search_query)
            | Q(description__icontains=normalized_search_query)
            | Q(normalized_category__icontains=normalized_search_query)
            | Q(public_profile__handle__icontains=normalized_search_query)
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
    )[page_offset:page_offset + page_limit]

    page_agents = [
        {
            "id": str(template.id),
            "name": template.display_name,
            "tagline": template.tagline,
            "description": template.description,
            "category": template.normalized_category,
            "categorySlug": public_template_category_slug(template),
            "publicProfileHandle": template.public_profile.handle,
            "templateSlug": template.slug,
            "templateUrl": public_template_detail_path(template),
            "isOfficial": template.is_official,
            "likeCount": template.like_count,
            "isLiked": template.is_liked,
        }
        for template in page_templates
    ]

    return {
        "agents": page_agents,
        "topCategories": top_categories,
        "totalAgents": total_agents,
        "libraryTotalAgents": library_total_agents,
        "officialTotalAgents": official_total_agents,
        "libraryTotalLikes": library_total_likes,
        "officialTotalLikes": official_total_likes,
        "officialOnly": official_only,
        "offset": page_offset,
        "limit": page_limit,
        "hasMore": (page_offset + page_limit) < total_agents,
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class LibraryView(TemplateView):
    template_name = "library.html"

    def dispatch(self, request, *args, **kwargs):
        self.selected_category = ""
        category_slug = kwargs.get("category_slug")
        if category_slug:
            legacy_template = _get_legacy_library_handle_template(category_slug)
            if legacy_template:
                return redirect(public_template_detail_path(legacy_template), permanent=True)
            self.selected_category = _resolve_category_from_slug(category_slug)
            canonical_slug = public_template_category_slug_from_label(self.selected_category)
            if category_slug != canonical_slug:
                return redirect(
                    "pages:library_category",
                    category_slug=canonical_slug,
                    permanent=True,
                )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_category = self.selected_category
        official_only = _parse_query_bool(self.request.GET.get("official"))
        page_title = (
            f"Official {selected_category} AI Agent Templates | Gobii"
            if selected_category and official_only
            else f"{selected_category} AI Agent Templates | Gobii"
            if selected_category
            else "Official AI Agent Templates | Gobii"
            if official_only
            else "AI Agent Templates & Workers | Gobii"
        )
        page_description = (
            f"Explore official Gobii {selected_category} AI agent templates maintained by Gobii for trusted workflows."
            if selected_category and official_only
            else f"Explore Gobii's {selected_category} AI agent templates and workers. Start from a shared template and customize it for your workflow."
            if selected_category
            else "Explore official Gobii AI agent templates maintained by Gobii for common workflows."
            if official_only
            else "Explore Gobii's library of AI agents and workers for sales, research, recruiting, operations, spreadsheets, email, and more. Start from a template or build your own."
        )
        context["page_name"] = "Agent Discovery"
        context["library_initial_category"] = selected_category
        context["library_initial_official_only"] = official_only
        context["library_page_title"] = page_title
        context["library_page_description"] = page_description
        context["library_initial_payload"] = _build_library_payload(
            self.request,
            category=selected_category,
            official_only=context["library_initial_official_only"],
        )
        return context


class LibraryAgentsAPIView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        category = _normalize_category(request.GET.get("category")) if request.GET.get("category") else ""
        search_query = str(request.GET.get("q") or "").strip()
        official_only = _parse_query_bool(request.GET.get("official"))
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

        return JsonResponse(
            _build_library_payload(
                request,
                category=category,
                search_query=search_query,
                official_only=official_only,
                limit=limit,
                offset=offset,
            )
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
                organization__isnull=True,
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
