import hashlib

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseNotModified
from django.templatetags.static import static

from config.vite import ViteManifestError, get_vite_asset

APP_PATH_PREFIX = "/app"
APP_SHELL_CACHE_CONTROL = (
    "public, max-age=300, s-maxage=3600, stale-while-revalidate=300, stale-if-error=86400"
)


def _format_vite_tags() -> str:
    try:
        asset = get_vite_asset("src/main.tsx")
    except ViteManifestError as error:
        return f"<!-- Vite asset error: {error} -->"

    tags: list[str] = []
    for href in asset.styles:
        tags.append(f'<link rel="stylesheet" href="{href}" />')

    scripts = list(asset.scripts)
    if scripts:
        tags.append(f'<script type="module" src="{scripts[0]}"></script>')

    for inline_module in asset.inline_modules:
        tags.append(f'<script type="module">{inline_module}</script>')

    for src in scripts[1:]:
        tags.append(f'<script type="module" src="{src}"></script>')

    return "\n".join(tags)


def _build_shell_html() -> str:
    vite_tags = _format_vite_tags()
    icon_url = static("images/noBgBlue.png")
    fonts_css = static("css/custom_fonts.css")
    pygments_css = static("css/pygments.css")
    globals_css = static("css/globals.css")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gobii App</title>
  <link rel="icon" type="image/png" href="{icon_url}" />
  <link rel="preload" as="image" href="{icon_url}" />
  <script src="https://cdn.tailwindcss.com?plugins=typography,forms,aspect-ratio,container-queries"></script>
  <link rel="stylesheet" href="{fonts_css}">
  <link rel="stylesheet" href="{pygments_css}">
  <link rel="stylesheet" href="{globals_css}">
  {vite_tags}
</head>
<body class="min-h-screen bg-white">
  <div id="gobii-frontend-root" data-app="immersive-app"></div>
</body>
</html>"""


class AppShellMiddleware:
    """Serve a static SPA shell for /app without touching session/auth middleware."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._cached_shell = None
        self._cached_etag = None

    def __call__(self, request):
        if not self._should_handle(request.path):
            return self.get_response(request)

        if request.method not in {"GET", "HEAD"}:
            return HttpResponseNotAllowed(["GET", "HEAD"])

        if self._cached_shell is None or settings.DEBUG:
            self._cached_shell = _build_shell_html()
            digest = hashlib.sha256(self._cached_shell.encode("utf-8")).hexdigest()
            self._cached_etag = f"\"{digest}\""

        request_etag = request.headers.get("If-None-Match")
        if self._etag_matches(request_etag):
            response = HttpResponseNotModified()
            response["ETag"] = self._cached_etag
            response["Cache-Control"] = APP_SHELL_CACHE_CONTROL
            return response

        response = HttpResponse(self._cached_shell, content_type="text/html; charset=utf-8")
        response["Cache-Control"] = APP_SHELL_CACHE_CONTROL
        if self._cached_etag:
            response["ETag"] = self._cached_etag
        return response

    @staticmethod
    def _should_handle(path: str) -> bool:
        return path == APP_PATH_PREFIX or path.startswith(f"{APP_PATH_PREFIX}/")

    def _etag_matches(self, request_etag: str | None) -> bool:
        if not request_etag or not self._cached_etag:
            return False
        candidates = [tag.strip() for tag in request_etag.split(",") if tag.strip()]
        for tag in candidates:
            if tag == self._cached_etag:
                return True
            if tag.startswith("W/") and tag[2:] == self._cached_etag:
                return True
        return False
