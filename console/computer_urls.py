from django.urls import path

from console.computer_api import (
    ComputerAssignmentAPIView,
    ComputerDetailAPIView,
    ComputerListAPIView,
    ComputerPairingApprovalAPIView,
    ComputerPairingDenyAPIView,
)


urlpatterns = [
    path("", ComputerListAPIView.as_view(), name="console-computer-list"),
    path(
        "pairings/<uuid:pairing_id>/",
        ComputerPairingApprovalAPIView.as_view(),
        name="console-computer-pairing",
    ),
    path(
        "pairings/<uuid:pairing_id>/deny/",
        ComputerPairingDenyAPIView.as_view(),
        name="console-computer-pairing-deny",
    ),
    path(
        "<uuid:device_id>/",
        ComputerDetailAPIView.as_view(),
        name="console-computer-detail",
    ),
    path(
        "<uuid:device_id>/assignment/",
        ComputerAssignmentAPIView.as_view(),
        name="console-computer-assignment",
    ),
]
