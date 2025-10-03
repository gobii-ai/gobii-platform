from django.urls import path

from .views import SetupWizardView, setup_complete_view

app_name = "setup"

urlpatterns = [
    path("", SetupWizardView.as_view(), name="wizard"),
    path("complete/", setup_complete_view, name="complete"),
]
