from django.urls import path
from django.views.generic import RedirectView

from pages.views import (
    AboutView,
    CareersView,
    ComparisonDetailView,
    ComparisonsIndexView,
    DataDeletionPolicyView,
    EditorialPolicyView,
    PrivacyPolicyView,
    ScaleCheckoutView,
    StartupCheckoutView,
    TermsOfServiceView,
)

from .feeds import BlogFeed
from .views import (
    BlogIndexView,
    BlogPostView,
    ContactView,
    PrequalifyView,
    PricingView,
    ShirtRedirectView,
    SupportView,
    TeamsView,
)

# Keep names consistent with pages app so existing {% url 'proprietary:...'%} still work
app_name = "proprietary"

urlpatterns = [
    path("shirt", ShirtRedirectView.as_view(), name="shirt_redirect"),
    path("shirt/", ShirtRedirectView.as_view(), name="shirt_redirect_slash"),
    path("pricing/", PricingView.as_view(), name="pricing"),
    path("teams/", TeamsView.as_view(), name="teams"),
    path("qualify/", PrequalifyView.as_view(), name="prequalify"),
    path("support/", SupportView.as_view(), name="support"),
    path("contact/", ContactView.as_view(), name="contact"),
    path("comparisons/", ComparisonsIndexView.as_view(), name="comparisons"),
    path("comparisons/<slug:slug>/", ComparisonDetailView.as_view(), name="comparison_detail"),
    path("about/", AboutView.as_view(), name="about"),
    # The team page was retired; keep the URL alive so existing inbound links and
    # blog author references resolve instead of 404ing.
    path("team/", RedirectView.as_view(pattern_name="proprietary:about", permanent=True), name="team"),
    path("careers/", CareersView.as_view(), name="careers"),
    path("tos/", TermsOfServiceView.as_view(), name="tos"),
    path("privacy/", PrivacyPolicyView.as_view(), name="privacy"),
    path("data-deletion/", DataDeletionPolicyView.as_view(), name="data_deletion"),
    path("editorial-policy/", EditorialPolicyView.as_view(), name="editorial_policy"),
    path("subscribe/startup/", StartupCheckoutView.as_view(), name="startup_checkout"),
    path("subscribe/pro/", StartupCheckoutView.as_view(), name="pro_checkout"),
    path("subscribe/scale/", ScaleCheckoutView.as_view(), name="scale_checkout"),

    # Blog URLs
    path("blog/", BlogIndexView.as_view(), name="blog_index"),
    path("blog/feed.xml", BlogFeed(), name="blog_feed"),
    path(
        "blog/newsletter-2026-05-19-remote-mcp/",
        RedirectView.as_view(pattern_name="pages:remote_mcp", permanent=True),
        name="remote_mcp_announcement_redirect",
    ),
    path("blog/<slug:slug>/", BlogPostView.as_view(), name="blog_post"),
]
