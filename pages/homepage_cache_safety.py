from django.conf import settings


ANONYMOUS_HOMEPAGE_CACHE_CONTROL = (
    "public, max-age=60, s-maxage=600, stale-while-revalidate=86400"
)
CSRF_WARMUP_CACHE_CONTROL = "no-store, max-age=0"


def is_cache_safe_anonymous_homepage_request(request) -> bool:
    if request.method not in {"GET", "HEAD"}:
        return False
    if getattr(request, "path_info", request.path) != "/":
        return False
    if request.META.get("QUERY_STRING"):
        return False
    if request.META.get("HTTP_AUTHORIZATION"):
        return False

    session_cookie_name = settings.SESSION_COOKIE_NAME
    return session_cookie_name not in request.COOKIES
