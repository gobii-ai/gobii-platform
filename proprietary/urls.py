from django.urls import path

from pages.views import AboutView, CareersView, TermsOfServiceView, PrivacyPolicyView, StartupCheckoutView
from .views import PricingView, SupportView

# Keep names consistent with pages app so existing {% url 'proprietary:...'%} still work
app_name = "proprietary"

urlpatterns = [
    path("pricing/", PricingView.as_view(), name="pricing"),
    path("support/", SupportView.as_view(), name="support"),
    path("about/", AboutView.as_view(), name="about"),
    path("careers/", CareersView.as_view(), name="careers"),
    path("tos/", TermsOfServiceView.as_view(), name="tos"),
    path("privacy/", PrivacyPolicyView.as_view(), name="privacy"),
    path("subscribe/startup/", StartupCheckoutView.as_view(), name="startup_checkout"),
    path("subscribe/pro/", StartupCheckoutView.as_view(), name="pro_checkout"),
]

