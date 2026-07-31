from django.urls import path

from api.computer_views import (
    computer_artifact_upload,
    computer_pairing_exchange,
    computer_pairing_start,
    computer_token_refresh,
)


urlpatterns = [
    path("pairings/", computer_pairing_start, name="computer-pairing-start"),
    path(
        "pairings/<uuid:pairing_id>/exchange/",
        computer_pairing_exchange,
        name="computer-pairing-exchange",
    ),
    path("tokens/refresh/", computer_token_refresh, name="computer-token-refresh"),
    path("artifacts/", computer_artifact_upload, name="computer-artifact-upload"),
]
