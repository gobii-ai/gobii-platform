from django.urls import path, include
from django.http import HttpResponse

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
    PretrainedWorkerDirectoryRedirectView,
    PretrainedWorkerDetailView,
    PretrainedWorkerHireView,
    PublicTemplateDetailView,
    PublicTemplateHireView,
    EngineeringProSignupView,
    SolutionView,
    MarketingContactRequestView,
    SolutionsSitemap,
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
sitemaps['solutions'] = SolutionsSitemap

urlpatterns = [
    path("", HomePage.as_view(), name="home"),
    path("spawn-agent/", HomeAgentSpawnView.as_view(), name="home_agent_spawn"),
    path("pretrained-workers/", PretrainedWorkerDirectoryRedirectView.as_view(), name="pretrained_worker_directory"),
    path("pretrained-workers/<slug:slug>/", PretrainedWorkerDetailView.as_view(), name="pretrained_worker_detail"),
    path("pretrained-workers/<slug:slug>/hire/", PretrainedWorkerHireView.as_view(), name="pretrained_worker_hire"),
    path("solutions/engineering/pro-signup/", EngineeringProSignupView.as_view(), name="engineering_pro_signup"),
    path("contact/request/", MarketingContactRequestView.as_view(), name="marketing_contact_request"),
    path("health/", health_check, name="health_check"),
    # Kubernetes health check endpoint - matches /healthz/ in BackendConfig
    path("healthz/", health_check, name="health_check_k8s"),

    # Documentation URLs
    path("docs/", DocsIndexRedirectView.as_view(), name="docs_index"),
    path("docs/<path:slug>/", MarkdownPageView.as_view(), name="markdown_page"),

    # Short landing page redirects
    path("g/<slug:code>/", LandingRedirectView.as_view(), name="landing_redirect"),

    # Solutions
    path("solutions/<slug:slug>/", SolutionView.as_view(), name="solution"),

    # Stripe webhooks
    path("stripe/", include("djstripe.urls", namespace="djstripe")),
    path("stripe/webhook/", djstripe_views.ProcessWebhookView.as_view(), name="stripe-webhook"),

    # Add sitemap URL pattern
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),

    # Make robots.txt available through Django
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),

    # Security.txt for vulnerability disclosure (RFC 9116)
    path('.well-known/security.txt', lambda r: HttpResponse(
        "Contact: mailto:security@gobii.ai\nExpires: 2027-01-01T17:00:00.000Z\n",
        content_type='text/plain',
    )),

    path('clear_signup_tracking', ClearSignupTrackingView.as_view(), name='clear_signup_tracking'),

    path('<slug:handle>/<slug:template_slug>/', PublicTemplateDetailView.as_view(), name='public_template_detail'),
    path('<slug:handle>/<slug:template_slug>/hire/', PublicTemplateHireView.as_view(), name='public_template_hire'),

]
