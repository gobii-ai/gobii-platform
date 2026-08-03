import json
import uuid
from json import JSONDecodeError
from typing import Any
from urllib.parse import urlencode

from django.core.cache import cache
from django.db.models import BooleanField, Case, CharField, Count, Exists, F, OuterRef, Q, Value, When
from django.db.models.functions import Lower
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView, View

from api.models import PersistentAgentTemplate, PersistentAgentTemplateLike, PersistentAgentTemplateUrlAlias
from pages.public_template_metadata import (
    SEO_TITLE_MAX_LENGTH,
    build_public_template_metadata,
    compose_meta_description,
    public_template_library_name,
)
from pages.public_template_urls import (
    public_template_category_slug,
    public_template_category_slug_aliases_from_label,
    public_template_category_slug_from_label,
    public_template_detail_path,
    public_template_route_slug,
)

LIBRARY_CACHE_KEY = "pages:library:payload:v2"
LIBRARY_OFFICIAL_CACHE_KEY = "pages:library:payload:official:v3"
LIBRARY_CATEGORY_SLUG_MAP_CACHE_KEY = "pages:library:category_slug_map:v2"
LIBRARY_CACHE_TTL_SECONDS = 120
LIBRARY_DEFAULT_PAGE_SIZE = 24
LIBRARY_MAX_PAGE_SIZE = 100


def invalidate_library_template_caches() -> None:
    cache.delete_many(
        [
            LIBRARY_CACHE_KEY,
            LIBRARY_OFFICIAL_CACHE_KEY,
            LIBRARY_CATEGORY_SLUG_MAP_CACHE_KEY,
        ]
    )


def _normalize_category(value: str | None) -> str:
    return (value or "").strip() or "Uncategorized"


def _library_page_title(selected_category: str, *, official_only: bool) -> str:
    if not selected_category:
        return (
            "Official AI Employee Templates for Business | Gobii"
            if official_only
            else "AI Employee Template Library: Business Roles | Gobii"
        )

    prefix = "Official " if official_only else ""
    suffix = " AI Employee Templates for Business | Gobii"
    title = f"{prefix}{selected_category}{suffix}"
    if len(title) <= SEO_TITLE_MAX_LENGTH:
        return title

    suffix = " AI Employee Templates | Gobii"
    title = f"{prefix}{selected_category}{suffix}"
    if len(title) <= SEO_TITLE_MAX_LENGTH:
        return title

    available_length = SEO_TITLE_MAX_LENGTH - len(prefix) - len(suffix)
    shortened_category = selected_category[: available_length + 1]
    if not shortened_category[available_length:available_length + 1].isspace():
        shortened_category = shortened_category[:available_length].rsplit(maxsplit=1)[0]
    return f"{prefix}{shortened_category.strip()}{suffix}"


def _library_queryset():
    return (
        PersistentAgentTemplate.objects.select_related("public_profile")
        .filter(organization__isnull=True, is_active=True, is_listed=True)
        .filter(Q(slug__gt="") | Q(code__gt=""))
    )


def _official_template_filter():
    return Q(is_official=True)


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


def _parse_library_page_number(value: str | None) -> int:
    if value in {None, ""}:
        return 1
    try:
        page_number = int(value)
    except (TypeError, ValueError) as error:
        raise Http404("Invalid library page") from error
    if page_number < 1:
        raise Http404("Invalid library page")
    return page_number


def _library_pagination_url(path: str, page_number: int, *, official_only: bool) -> str:
    query: list[tuple[str, str]] = []
    if official_only:
        query.append(("official", "true"))
    if page_number > 1:
        query.append(("page", str(page_number)))
    encoded_query = urlencode(query)
    return f"{path}?{encoded_query}" if encoded_query else path


def _build_top_categories(*, official_only: bool = False) -> list[dict[str, Any]]:
    queryset = _library_queryset()
    if official_only:
        queryset = queryset.filter(_official_template_filter())
    category_rows = (
        queryset.annotate(normalized_category=_normalized_category_expression())
        .values("normalized_category")
        .annotate(count=Count("id"))
        .order_by("-count", Lower("normalized_category"))[:10]
    )
    return [
        {
            "name": row["normalized_category"],
            "slug": public_template_category_slug_from_label(row["normalized_category"]),
            "count": row["count"],
        }
        for row in category_rows
    ]


def _get_top_categories(*, official_only: bool = False) -> list[dict[str, Any]]:
    cache_key = LIBRARY_OFFICIAL_CACHE_KEY if official_only else LIBRARY_CACHE_KEY
    cached = cache.get(cache_key)
    if isinstance(cached, list):
        valid_items = all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("slug"), str)
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
    normalized_template_slug = str(template_slug or "").strip()
    if not normalized_template_slug:
        return None

    template = _library_queryset().filter(
        public_profile__handle="library",
        slug=normalized_template_slug,
    ).first()
    if template:
        return template

    alias = (
        PersistentAgentTemplateUrlAlias.objects.select_related("template", "template__public_profile")
        .filter(
            Q(handle="library") | Q(handle="", public_profile__handle="library"),
            slug=normalized_template_slug,
            template__is_active=True,
            template__is_listed=True,
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
    official_queryset = library_queryset.filter(_official_template_filter())
    library_total_agents = library_queryset.count()
    official_total_agents = official_queryset.count()
    library_total_likes = (
        PersistentAgentTemplateLike.objects.filter(template__in=library_queryset).count()
    )
    official_total_likes = (
        PersistentAgentTemplateLike.objects.filter(template__in=official_queryset).count()
    )

    filtered_queryset = library_queryset
    if official_only:
        filtered_queryset = filtered_queryset.filter(_official_template_filter())

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

    page_agents = []
    for template in page_templates:
        template_metadata = build_public_template_metadata(template)
        page_agents.append(
            {
                "id": str(template.id),
                "name": public_template_library_name(template),
                "tagline": template_metadata.tagline,
                "description": template.description,
                "seoDescription": template_metadata.description,
                "category": template.normalized_category,
                "categorySlug": public_template_category_slug(template),
                "publicProfileHandle": (
                    template.public_profile.handle
                    if template.public_profile_id
                    else ""
                ),
                "templateSlug": public_template_route_slug(template),
                "templateUrl": public_template_detail_path(template),
                "isOfficial": template.is_official,
                "likeCount": template.like_count,
                "isLiked": template.is_liked,
            }
        )

    return {
        "agents": page_agents,
        "topCategories": top_categories,
        "selectedCategorySlug": (
            public_template_category_slug_from_label(normalized_category)
            if normalized_category
            else ""
        ),
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
        self.page_number = _parse_library_page_number(request.GET.get("page"))
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
        page_title = _library_page_title(
            selected_category,
            official_only=official_only,
        )
        description_source = (
            f"Explore official Gobii {selected_category} AI agent templates maintained by Gobii for trusted workflows."
            if selected_category and official_only
            else f"Explore Gobii's {selected_category} AI employee templates for role-specific workflows, with selected AI agent terminology and community-created options."
            if selected_category
            else "Explore official Gobii AI employee templates maintained for trusted, reusable business workflows."
            if official_only
            else "Explore Gobii's AI Employee Template Library for sales, research, recruiting, operations, finance, and more. Choose a role and customize its workflow."
        )
        page_description = compose_meta_description(
            explicit_description=description_source,
            description="",
            tagline="",
            display_name="AI Employee Template Library",
        )
        page_offset = (self.page_number - 1) * LIBRARY_DEFAULT_PAGE_SIZE
        initial_payload = _build_library_payload(
            self.request,
            category=selected_category,
            official_only=official_only,
            offset=page_offset,
        )
        if self.page_number > 1 and page_offset >= initial_payload["totalAgents"]:
            raise Http404("Library page does not exist")

        total_pages = max(
            1,
            (initial_payload["totalAgents"] + LIBRARY_DEFAULT_PAGE_SIZE - 1)
            // LIBRARY_DEFAULT_PAGE_SIZE,
        )
        canonical_path = _library_pagination_url(
            self.request.path,
            self.page_number,
            official_only=False,
        )
        context["page_name"] = "AI Employee Template Library"
        context["library_initial_category"] = selected_category
        context["library_initial_official_only"] = official_only
        context["library_page_title"] = page_title
        context["library_page_description"] = page_description
        context["library_schema_name"] = (
            f"{selected_category} AI Employee Templates"
            if selected_category
            else "Gobii AI Employee Template Library"
        )
        context["library_item_list_name"] = (
            f"Popular {selected_category} AI Employee Templates"
            if selected_category
            else "Popular Gobii AI Employee Templates"
        )
        context["library_initial_payload"] = initial_payload
        context["library_current_page"] = self.page_number
        context["library_total_pages"] = total_pages
        context["library_previous_page_url"] = (
            _library_pagination_url(
                self.request.path,
                self.page_number - 1,
                official_only=official_only,
            )
            if self.page_number > 1
            else ""
        )
        context["library_next_page_url"] = (
            _library_pagination_url(
                self.request.path,
                self.page_number + 1,
                official_only=official_only,
            )
            if initial_payload["hasMore"]
            else ""
        )
        context["canonical_url"] = self.request.build_absolute_uri(canonical_path)
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
            _library_queryset()
            .filter(id=agent_uuid)
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
