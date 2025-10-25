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
    PretrainedWorkerTemplateSitemap,
    LandingRedirectView,
    ClearSignupTrackingView,
    PretrainedWorkerDirectoryView,
    PretrainedWorkerDetailView,
    PretrainedWorkerHireView,
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

sitemaps['pretrained_workers'] = PretrainedWorkerTemplateSitemap

urlpatterns = [
    path("", HomePage.as_view(), name="home"),
    path("spawn-agent/", HomeAgentSpawnView.as_view(), name="home_agent_spawn"),
    path("pretrained-workers/", PretrainedWorkerDirectoryView.as_view(), name="pretrained_worker_directory"),
    path("pretrained-workers/<slug:slug>/", PretrainedWorkerDetailView.as_view(), name="pretrained_worker_detail"),
    path("pretrained-workers/<slug:slug>/hire/", PretrainedWorkerHireView.as_view(), name="pretrained_worker_hire"),
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
