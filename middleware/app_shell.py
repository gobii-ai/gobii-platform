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


def _format_segment_snippet() -> str:
    """Generate Segment analytics snippet if configured."""
    write_key = getattr(settings, "SEGMENT_WEB_WRITE_KEY", None)
    if not write_key:
        # Provide a no-op stub in debug mode or when not configured
        return """<script>
    window.analytics = window.analytics || {
      page: function() {},
      track: function() {},
      identify: function() {},
      ready: function(cb) { if (typeof cb === 'function') cb(); },
    };
  </script>"""

    return f"""<script>
    !function(){{var i="analytics",analytics=window[i]=window[i]||[];if(!analytics.initialize)if(analytics.invoked)window.console&&console.error&&console.error("Segment snippet included twice.");else{{analytics.invoked=!0;analytics.methods=["trackSubmit","trackClick","trackLink","trackForm","pageview","identify","reset","group","track","ready","alias","debug","page","screen","once","off","on","addSourceMiddleware","addIntegrationMiddleware","setAnonymousId","addDestinationMiddleware","register"];analytics.factory=function(e){{return function(){{if(window[i].initialized)return window[i][e].apply(window[i],arguments);var n=Array.prototype.slice.call(arguments);if(["track","screen","alias","group","page","identify"].indexOf(e)>-1){{var c=document.querySelector("link[rel='canonical']");n.push({{__t:"bpc",c:c&&c.getAttribute("href")||void 0,p:location.pathname,u:location.href,s:location.search,t:document.title,r:document.referrer}})}}n.unshift(e);analytics.push(n);return analytics}}}};for(var n=0;n<analytics.methods.length;n++){{var key=analytics.methods[n];analytics[key]=analytics.factory(key)}}analytics.load=function(key,n){{var t=document.createElement("script");t.type="text/javascript";t.async=!0;t.setAttribute("data-global-segment-analytics-key",i);t.src="https://cdn.segment.com/analytics.js/v1/" + key + "/analytics.min.js";var r=document.getElementsByTagName("script")[0];r.parentNode.insertBefore(t,r);analytics._loadOptions=n}};analytics.SNIPPET_VERSION="5.2.0";}}}}();
    analytics.load("{write_key}");
    analytics.addSourceMiddleware(({{ payload, next }}) => {{
      if (!payload.obj.properties) payload.obj.properties = {{}};
      payload.obj.properties.medium = 'Web';
      payload.obj.properties.frontend = true;
      next(payload);
    }});
    analytics.page('App', 'Immersive App');
  </script>"""


def _build_shell_html() -> str:
    vite_tags = _format_vite_tags()
    segment_snippet = _format_segment_snippet()
    icon_url = static("images/noBgBlue.png")
    fonts_css = static("css/custom_fonts.css")
    pygments_css = static("css/pygments.css")
    globals_css = static("css/globals.css")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, interactive-widget=resizes-content">
  <title>Gobii App</title>
  <link rel="icon" type="image/png" href="{icon_url}" />
  <link rel="preload" as="image" href="{icon_url}" />
  <script src="https://cdn.tailwindcss.com?plugins=typography,forms,aspect-ratio,container-queries"></script>
  <link rel="stylesheet" href="{fonts_css}">
  <link rel="stylesheet" href="{pygments_css}">
  <link rel="stylesheet" href="{globals_css}">
  {segment_snippet}
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
