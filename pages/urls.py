from django.urls import path, include

from config.settings import GOBII_PROPRIETARY_MODE
from proprietary.views import BlogSitemap
from .views import (
    MarkdownPageView,
    DocsIndexRedirectView,
    HomePage,
    HomeAgentSpawnView,
    TermsOfServiceView,
    PrivacyPolicyView,
    health_check,
    AboutView,
    CareersView,
    StartupCheckoutView,
    StaticViewSitemap,
    AIEmployeeTemplateSitemap,
    LandingRedirectView,
    ClearSignupTrackingView,
    AIEmployeeDirectoryView,
    AIEmployeeDetailView,
    AIEmployeeHireView,
)

from djstripe import views as djstripe_views
from django.contrib.sitemaps.views import sitemap
from django.views.generic.base import TemplateView

app_name = "pages"

sitemaps = {
    'static': StaticViewSitemap,
}

if GOBII_PROPRIETARY_MODE:
    sitemaps['blog'] = BlogSitemap

sitemaps['ai_employees'] = AIEmployeeTemplateSitemap

urlpatterns = [
    path("", HomePage.as_view(), name="home"),
    path("spawn-agent/", HomeAgentSpawnView.as_view(), name="home_agent_spawn"),
    path("ai-employees/", AIEmployeeDirectoryView.as_view(), name="ai_employee_directory"),
    path("ai-employees/<slug:slug>/", AIEmployeeDetailView.as_view(), name="ai_employee_detail"),
    path("ai-employees/<slug:slug>/hire/", AIEmployeeHireView.as_view(), name="ai_employee_hire"),
    path("health/", health_check, name="health_check"),
    # Kubernetes health check endpoint - matches /healthz/ in BackendConfig
    path("healthz/", health_check, name="health_check_k8s"),

    # Documentation URLs
    path("docs/", DocsIndexRedirectView.as_view(), name="docs_index"),
    path("docs/<path:slug>/", MarkdownPageView.as_view(), name="markdown_page"),

    # Short landing page redirects
    path("g/<slug:code>/", LandingRedirectView.as_view(), name="landing_redirect"),

    # Stripe webhooks
    path("stripe/", include("djstripe.urls", namespace="djstripe")),
    path("stripe/webhook/", djstripe_views.ProcessWebhookView.as_view(), name="stripe-webhook"),

    # Add sitemap URL pattern
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),

    # Make robots.txt available through Django
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),

    path('clear_signup_tracking', ClearSignupTrackingView.as_view(), name='clear_signup_tracking'),

]
