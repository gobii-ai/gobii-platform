from django.urls import path, include
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
    StartupCheckoutView, StaticViewSitemap, LandingRedirectView, ClearSignupTrackingView,
)

from djstripe import views as djstripe_views
from django.contrib.sitemaps.views import sitemap
from django.views.generic.base import TemplateView

app_name = "pages"

sitemaps = {
    'static': StaticViewSitemap,
}

urlpatterns = [
    path("", HomePage.as_view(), name="home"),
    path("spawn-agent/", HomeAgentSpawnView.as_view(), name="home_agent_spawn"),
    path("health/", health_check, name="health_check"),
    path("healthz/", health_check, name="health_check_k8s"),  # Kubernetes health check endpoint - matches /healthz/ in BackendConfig
    
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
