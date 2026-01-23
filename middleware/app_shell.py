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
    if settings.DEBUG or not write_key:
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
    var maxRetries = 3;
    var baseDelay = 1000;

    function firePixels(data) {
      var p = data.pixels || {};
      var val = data.registrationValue || 0;
      var cur = 'USD';

      // Google Analytics
      if (p.ga && typeof window.gtag === 'function') {
        gtag('event', 'sign_up', { method: 'email', value: val, currency: cur });
      }

      // Reddit
      if (p.reddit && typeof window.rdt === 'function') {
        rdt('track', 'SignUp', {
          email: data.emailHash,
          externalId: data.idHash,
          conversionId: data.eventId,
          value: val,
          currency: cur
        });
      }

      // TikTok
      if (p.tiktok && window.ttq && typeof window.ttq.track === 'function') {
        ttq.track('CompleteRegistration', {
          event_id: data.eventId,
          external_id: data.idHash,
          email: data.emailHash,
          value: val,
          currency: cur
        });
      }

      // Meta/Facebook
      if (p.meta && typeof window.fbq === 'function') {
        fbq('track', 'CompleteRegistration', {
          value: val,
          currency: cur
        }, {
          external_id: data.idHash,
          em: data.emailHash,
          eventID: data.eventId
        });
      }

      // LinkedIn
      if (p.linkedin && typeof window.lintrk === 'function') {
        window.lintrk('track', { conversion_id: p.linkedin });
      }

      // Track successful pixel fire
      if (window.analytics && typeof window.analytics.track === 'function') {
        window.analytics.track('Signup Pixels Fired', { eventId: data.eventId, source: 'app_shell' });
      }
    }

    function fetchWithRetry(attempt) {
      fetch('/clear_signup_tracking', { credentials: 'same-origin' })
        .then(function(r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function(data) {
          if (data.tracking) firePixels(data);
        })
        .catch(function(err) {
          if (attempt < maxRetries) {
            var delay = baseDelay * Math.pow(2, attempt - 1);
            setTimeout(function() { fetchWithRetry(attempt + 1); }, delay);
          } else if (window.analytics && typeof window.analytics.track === 'function') {
            window.analytics.track('Signup Pixel Fetch Failed', {
              error: err.message || 'Unknown error',
              attempts: maxRetries,
              source: 'app_shell'
            });
          }
        });
    }

    fetchWithRetry(1);
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
    gtag('config', '{ga_id}', {{ anonymize_ip: true }});
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
    icon_url = static("images/noBgBlue.png")
    fonts_css = static("css/custom_fonts.css")
    pygments_css = static("css/pygments.css")
    globals_css = static("css/globals.css")
    csrf_cookie_name = getattr(settings, "CSRF_COOKIE_NAME", "csrftoken") or "csrftoken"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, interactive-widget=resizes-content">
  <meta name="csrf-cookie-name" content="{csrf_cookie_name}">
  <title>Gobii App</title>
  <link rel="icon" type="image/png" href="{icon_url}" />
  <link rel="preload" as="image" href="{icon_url}" />
  <script src="https://cdn.tailwindcss.com?plugins=typography,forms,aspect-ratio,container-queries"></script>
  <link rel="stylesheet" href="{fonts_css}">
  <link rel="stylesheet" href="{pygments_css}">
  <link rel="stylesheet" href="{globals_css}">
  {pixel_loaders}
  {segment_snippet}
  {signup_tracking}
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
