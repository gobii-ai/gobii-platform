from django.urls import path

from .views import SetupWizardView, setup_complete_view, test_llm_connection

app_name = "setup"

urlpatterns = [
    path("", SetupWizardView.as_view(), name="wizard"),
    path("complete/", setup_complete_view, name="complete"),
    path("test-llm/", test_llm_connection, name="test_llm"),
]
