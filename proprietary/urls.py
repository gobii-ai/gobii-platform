from django.urls import path
from .views import PricingView, SupportView

# Keep names consistent with pages app so existing {% url 'pages:...'%} still work
app_name = "proprietary"

urlpatterns = [
    path("pricing/", PricingView.as_view(), name="pricing"),
    path("support/", SupportView.as_view(), name="support"),
]

