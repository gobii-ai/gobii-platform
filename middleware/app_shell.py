import hashlib
import json
from urllib.parse import urlsplit

from django.contrib.auth.views import redirect_to_login
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseNotModified, HttpResponseRedirect
from django.templatetags.static import static
from django.urls import reverse

from api.services.system_settings import get_max_file_size
from config.vite import ViteManifestError, get_vite_asset
from util.integrations import pipedream_status

APP_PATH_PREFIX = "/app"
APP_SHELL_BYPASS_PATHS = (
    f"{APP_PATH_PREFIX}/email/oauth/callback/",
)
APP_PROTECTED_PATH_PREFIX = f"{APP_PATH_PREFIX}/agents"
APP_BILLING_PATH_PREFIX = f"{APP_PATH_PREFIX}/billing"
APP_API_KEYS_PATH_PREFIX = f"{APP_PATH_PREFIX}/api-keys"
APP_AGENT_COLLABORATOR_INVITES_PATH_PREFIX = f"{APP_PATH_PREFIX}/agent-collaborator-invites"
APP_TEAM_PATH_PREFIX = f"{APP_PATH_PREFIX}/team"
APP_ORGANIZATION_PATH_PREFIX = f"{APP_PATH_PREFIX}/organization"
APP_ORGANIZATION_INVITES_PATH_PREFIX = f"{APP_PATH_PREFIX}/organizations/invites"
APP_PROFILE_PATH_PREFIX = f"{APP_PATH_PREFIX}/profile"
APP_SECRETS_PATH_PREFIX = f"{APP_PATH_PREFIX}/secrets"
APP_USAGE_PATH_PREFIX = f"{APP_PATH_PREFIX}/usage"
APP_INTEGRATIONS_PATH_PREFIX = f"{APP_PATH_PREFIX}/integrations"
APP_LOGIN_REQUIRED_PATH_PREFIXES = (
    APP_PROTECTED_PATH_PREFIX,
    APP_BILLING_PATH_PREFIX,
    APP_API_KEYS_PATH_PREFIX,
    APP_AGENT_COLLABORATOR_INVITES_PATH_PREFIX,
    APP_TEAM_PATH_PREFIX,
    APP_ORGANIZATION_PATH_PREFIX,
    APP_ORGANIZATION_INVITES_PATH_PREFIX,
    APP_PROFILE_PATH_PREFIX,
    APP_SECRETS_PATH_PREFIX,
    APP_USAGE_PATH_PREFIX,
    APP_INTEGRATIONS_PATH_PREFIX,
)
APP_LOGIN_REQUIRED_SUBPATH_PREFIXES = tuple(f"{prefix}/" for prefix in APP_LOGIN_REQUIRED_PATH_PREFIXES)
APP_SHELL_CACHE_CONTROL = "no-cache, must-revalidate"
APP_ROBOTS_HEADER_VALUE = "noindex, follow"


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
    write_key = settings.SEGMENT_WEB_WRITE_KEY
    segment_enabled = bool(write_key and (not settings.DEBUG or settings.SEGMENT_WEB_ENABLE_IN_DEBUG))
    enabled_js = "true" if segment_enabled else "false"
    write_key_json = json.dumps(write_key)
    return f"""<script>
    (function() {{
      var segmentEnabled = window.GobiiSegmentBootstrap && window.GobiiSegmentBootstrap.init({{
        enabled: {enabled_js},
        writeKey: {write_key_json},
        defaultProperties: {{
          medium: 'Web',
          frontend: true
        }}
      }});
      if (!segmentEnabled) {{
        return;
      }}
      analytics.page('App', 'Immersive App');
    }})();
  </script>"""


def _format_signup_tracking_snippet() -> str:
    """Generate signup tracking snippet that fetches data from API.

    Since the app shell is statically cached, we can't include user-specific
    tracking data directly. Instead, this script fetches tracking data from
    the clear_signup_tracking endpoint which has session access.
    """
    if settings.DEBUG:
        return "<!-- Signup tracking disabled in debug mode -->"

    proprietary = getattr(settings, "GOBII_PROPRIETARY_MODE", False)
    if not proprietary:
        return "<!-- Signup tracking disabled (non-proprietary mode) -->"

    return """<script>
  (function() {
    function fireSignupTracking() {
      if (!window.GobiiSignupTracking || typeof window.GobiiSignupTracking.fetchAndFire !== 'function') {
        return;
      }
      window.GobiiSignupTracking.fetchAndFire({
        endpoint: '/clear_signup_tracking',
        source: 'app_shell'
      });
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fireSignupTracking, { once: true });
    } else {
      fireSignupTracking();
    }
  })();
  </script>"""


def _format_pixel_loaders() -> str:
    """Generate pixel loader scripts for tracking platforms."""
    if settings.DEBUG:
        return "<!-- Pixel loaders disabled in debug mode -->"

    proprietary = getattr(settings, "GOBII_PROPRIETARY_MODE", False)
    snippets = []

    # Google Analytics
    ga_id = getattr(settings, "GA_MEASUREMENT_ID", None)
    if ga_id:
        snippets.append(f"""<script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', '{ga_id}', {{ anonymize_ip: true, send_page_view: false }});
  </script>""")

    if not proprietary:
        return "\n  ".join(snippets) if snippets else ""

    # Reddit Pixel
    reddit_id = getattr(settings, "REDDIT_PIXEL_ID", None)
    if reddit_id:
        snippets.append(f"""<script>
  !function(w,d){{if(!w.rdt){{var p=w.rdt=function(){{p.sendEvent?p.sendEvent.apply(p,arguments):p.callQueue.push(arguments)}};p.callQueue=[];var t=d.createElement("script");t.src="https://www.redditstatic.com/ads/pixel.js",t.async=!0;var s=d.getElementsByTagName("script")[0];s.parentNode.insertBefore(t,s)}}}}(window,document);
  rdt('init','{reddit_id}');
  rdt('track', 'PageVisit');
  </script>""")

    # TikTok Pixel
    tiktok_id = getattr(settings, "TIKTOK_PIXEL_ID", None)
    if tiktok_id:
        snippets.append(f"""<script>
  !function (w, d, t) {{
    w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie","holdConsent","revokeConsent","grantConsent"],ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}}};for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.instance=function(t){{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)ttq.setAndDefer(e,ttq.methods[n]);return e}},ttq.load=function(e,n){{var r="https://analytics.tiktok.com/i18n/pixel/events.js",o=n&&n.partner;ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._i[e]._u=r,ttq._t=ttq._t||{{}},ttq._t[e]=+new Date,ttq._o=ttq._o||{{}},ttq._o[e]=n||{{}};n=document.createElement("script");n.type="text/javascript";n.async=!0;n.src=r+"?sdkid="+e+"&lib="+t;var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(n,a)}};
    ttq.load('{tiktok_id}');
    ttq.page();
  }}(window, document, 'ttq');
  </script>""")

    # Meta Pixel
    meta_id = getattr(settings, "META_PIXEL_ID", None)
    if meta_id:
        snippets.append(f"""<script>
  !function(f,b,e,v,n,t,s)
  {{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
  n.callMethod.apply(n,arguments):n.queue.push(arguments)}};
  if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
  n.queue=[];t=b.createElement(e);t.async=!0;
  t.src=v;s=b.getElementsByTagName(e)[0];
  s.parentNode.insertBefore(t,s)}}(window, document,'script',
  'https://connect.facebook.net/en_US/fbevents.js');
  fbq('init', '{meta_id}');
  fbq('track', 'PageView');
  </script>""")

    # LinkedIn Pixel
    linkedin_id = getattr(settings, "LINKEDIN_PARTNER_ID", None)
    if linkedin_id:
        snippets.append(f"""<script>
  _linkedin_partner_id = "{linkedin_id}";
  window._linkedin_data_partner_ids = window._linkedin_data_partner_ids || [];
  window._linkedin_data_partner_ids.push(_linkedin_partner_id);
  (function(l){{if(!l){{window.lintrk=function(a,b){{window.lintrk.q.push([a,b])}};window.lintrk.q=[]}}var s=document.getElementsByTagName("script")[0];var b=document.createElement("script");b.type="text/javascript";b.async=true;b.src="https://snap.licdn.com/li.lms-analytics/insight.min.js";s.parentNode.insertBefore(b,s);}})(window.lintrk);
  </script>""")

    return "\n  ".join(snippets) if snippets else ""


def _build_shell_html() -> str:
    vite_tags = _format_vite_tags()
    segment_snippet = _format_segment_snippet()
    pixel_loaders = _format_pixel_loaders()
    signup_tracking = _format_signup_tracking_snippet()
    segment_bootstrap_js = static("js/segment_bootstrap.js")
    analytics_js = static("js/gobii_analytics.js")
    signup_tracking_js = static("js/signup_tracking.js")
    icon_url = static("images/gobii_fish.png")
    fonts_css = static("css/custom_fonts.css")
    pygments_css = static("css/pygments.css")
    globals_css = static("css/globals.css")
    google_tag_preconnect = (
        '<link rel="preconnect" href="https://www.googletagmanager.com" />'
        if settings.GA_MEASUREMENT_ID and not settings.DEBUG
        else ""
    )
    csrf_cookie_name = getattr(settings, "CSRF_COOKIE_NAME", "csrftoken") or "csrftoken"
    max_chat_upload_size_bytes = get_max_file_size()
    max_chat_upload_size_attr = (
        f' data-max-chat-upload-size-bytes="{max_chat_upload_size_bytes}"'
        if max_chat_upload_size_bytes
        else ""
    )
    native_integrations_url = reverse("console-native-integration-list")
    native_integrations_attr = f' data-native-integrations-url="{native_integrations_url}"'
    pipedream_attrs = ""
    if pipedream_status().enabled:
        pipedream_apps_url = reverse("console-pipedream-apps")
        pipedream_app_search_url = reverse("console-pipedream-app-search")
        pipedream_attrs = (
            f' data-pipedream-apps-url="{pipedream_apps_url}"'
            f' data-pipedream-app-search-url="{pipedream_app_search_url}"'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, interactive-widget=resizes-content">
  <meta name="robots" content="{APP_ROBOTS_HEADER_VALUE}">
  <meta name="csrf-cookie-name" content="{csrf_cookie_name}">
  <title>Gobii App</title>
  <link rel="icon" type="image/png" href="{icon_url}" />
  <link rel="preconnect" href="https://cdn.tailwindcss.com" />
  {google_tag_preconnect}
  <link rel="preload" as="image" href="{icon_url}" />
  <script src="https://cdn.tailwindcss.com?plugins=typography,forms,aspect-ratio,container-queries"></script>
  <link rel="stylesheet" href="{fonts_css}">
  <link rel="stylesheet" href="{pygments_css}">
  <link rel="stylesheet" href="{globals_css}">
  {pixel_loaders}
  <script src="{segment_bootstrap_js}"></script>
  {segment_snippet}
  <script src="{analytics_js}" defer></script>
  <script src="{signup_tracking_js}" defer></script>
  {signup_tracking}
  {vite_tags}
</head>
<body class="min-h-screen bg-white">
  <div id="gobii-frontend-root" data-app="immersive-app"{max_chat_upload_size_attr}{native_integrations_attr}{pipedream_attrs}></div>
</body>
</html>"""


class AppShellMiddleware:
    """Serve the immersive app shell and gate protected immersive routes."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._cached_shell = None
        self._cached_etag = None

    def __call__(self, request):
        if request.method in {"GET", "HEAD"}:
            legacy_console_redirect = self._legacy_console_redirect(request)
            if legacy_console_redirect is not None:
                if self._requires_login(urlsplit(legacy_console_redirect).path) and not request.user.is_authenticated:
                    return redirect_to_login(legacy_console_redirect, login_url=reverse("account_login"))
                return HttpResponseRedirect(legacy_console_redirect)

        if not self._should_handle(request.path):
            return self.get_response(request)

        if request.method not in {"GET", "HEAD"}:
            return self._with_robots_header(HttpResponseNotAllowed(["GET", "HEAD"]))

        if self._requires_login(request.path) and not request.user.is_authenticated:
            return self._with_robots_header(
                redirect_to_login(request.get_full_path(), login_url=reverse("account_login"))
            )

        canonical_redirect = self._canonical_redirect_path(request)
        if canonical_redirect is not None:
            return self._with_robots_header(HttpResponseRedirect(canonical_redirect))

        if request.user.is_authenticated:
            self._apply_context_query(request)

        if request.path == APP_BILLING_PATH_PREFIX or request.path.startswith(f"{APP_BILLING_PATH_PREFIX}/"):
            from console.billing_return import process_billing_return

            process_billing_return(request)

        if self._cached_shell is None or settings.DEBUG:
            self._cached_shell = _build_shell_html()
            digest = hashlib.sha256(self._cached_shell.encode("utf-8")).hexdigest()
            self._cached_etag = f"\"{digest}\""

        request_etag = request.headers.get("If-None-Match")
        if self._etag_matches(request_etag):
            response = HttpResponseNotModified()
            response["ETag"] = self._cached_etag
            response["Cache-Control"] = APP_SHELL_CACHE_CONTROL
            return self._with_robots_header(response)

        response = HttpResponse(self._cached_shell, content_type="text/html; charset=utf-8")
        response["Cache-Control"] = APP_SHELL_CACHE_CONTROL
        if self._cached_etag:
            response["ETag"] = self._cached_etag
        return self._with_robots_header(response)

    @staticmethod
    def _should_handle(path: str) -> bool:
        normalized_path = path if path.endswith("/") else f"{path}/"
        if normalized_path in APP_SHELL_BYPASS_PATHS:
            return False
        return path == APP_PATH_PREFIX or path.startswith(f"{APP_PATH_PREFIX}/")

    @staticmethod
    def _with_robots_header(response):
        response["X-Robots-Tag"] = APP_ROBOTS_HEADER_VALUE
        return response

    @staticmethod
    def _canonical_redirect_path(request) -> str | None:
        if request.path != APP_ORGANIZATION_PATH_PREFIX and not request.path.startswith(f"{APP_ORGANIZATION_PATH_PREFIX}/"):
            return None

        target_path = request.path.replace(APP_ORGANIZATION_PATH_PREFIX, APP_TEAM_PATH_PREFIX, 1)
        query_string = request.META.get("QUERY_STRING")
        return f"{target_path}?{query_string}" if query_string else target_path

    @staticmethod
    def _legacy_console_redirect(request) -> str | None:
        if not settings.LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED:
            return None
        if not request.path.startswith("/console/"):
            return None
        from console.legacy_redirects import get_legacy_console_redirect_path

        return get_legacy_console_redirect_path(request)

    @staticmethod
    def _requires_login(path: str) -> bool:
        return path in APP_LOGIN_REQUIRED_PATH_PREFIXES or path.startswith(APP_LOGIN_REQUIRED_SUBPATH_PREFIXES)

    @staticmethod
    def _apply_context_query(request) -> None:
        context_type = (request.GET.get("context_type") or "").strip().lower()
        context_id = (request.GET.get("context_id") or "").strip()
        if not context_type or not context_id:
            return

        try:
            from console.context_helpers import resolve_console_context

            resolved = resolve_console_context(
                request.user,
                request.session,
                override={"type": context_type, "id": context_id},
            )
        except PermissionDenied:
            return

        current_context = resolved.current_context
        request.session["context_type"] = current_context.type
        request.session["context_id"] = current_context.id
        request.session["context_name"] = current_context.name
        request.session.modified = True

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
