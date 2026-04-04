from django.urls import path

from app_api.views import (
    NativeAppAgentFsNodeDownloadAPIView,
    NativeAppAgentMessageCreateAPIView,
    NativeAppAgentRosterAPIView,
    NativeAppAgentSessionEndAPIView,
    NativeAppAgentSessionHeartbeatAPIView,
    NativeAppAgentSessionStartAPIView,
    NativeAppAgentTimelineAPIView,
    NativeAppEmailResendVerificationAPIView,
    NativeAppHumanInputRequestBatchResponseAPIView,
    NativeAppHumanInputRequestResponseAPIView,
    NativeAppLogoutAPIView,
    NativeAppMeAPIView,
    NativeAppRefreshAPIView,
    NativeAppSignInAPIView,
    NativeAppSignUpAPIView,
)


app_name = "app_api"


urlpatterns = [
    path("auth/sign-up/", NativeAppSignUpAPIView.as_view(), name="auth_sign_up"),
    path("auth/sign-in/", NativeAppSignInAPIView.as_view(), name="auth_sign_in"),
    path("auth/refresh/", NativeAppRefreshAPIView.as_view(), name="auth_refresh"),
    path("auth/logout/", NativeAppLogoutAPIView.as_view(), name="auth_logout"),
    path(
        "auth/email/resend-verification/",
        NativeAppEmailResendVerificationAPIView.as_view(),
        name="auth_email_resend_verification",
    ),
    path("me/", NativeAppMeAPIView.as_view(), name="me"),
    path("agents/", NativeAppAgentRosterAPIView.as_view(), name="agent_roster"),
    path("agents/<uuid:agent_id>/timeline/", NativeAppAgentTimelineAPIView.as_view(), name="agent_timeline"),
    path("agents/<uuid:agent_id>/messages/", NativeAppAgentMessageCreateAPIView.as_view(), name="agent_message_create"),
    path(
        "agents/<uuid:agent_id>/human-input-requests/respond-batch/",
        NativeAppHumanInputRequestBatchResponseAPIView.as_view(),
        name="agent_human_input_batch_respond",
    ),
    path(
        "agents/<uuid:agent_id>/human-input-requests/<uuid:request_id>/respond/",
        NativeAppHumanInputRequestResponseAPIView.as_view(),
        name="agent_human_input_respond",
    ),
    path(
        "agents/<uuid:agent_id>/files/download/",
        NativeAppAgentFsNodeDownloadAPIView.as_view(),
        name="agent_fs_download",
    ),
    path(
        "agents/<uuid:agent_id>/sessions/start/",
        NativeAppAgentSessionStartAPIView.as_view(),
        name="agent_session_start",
    ),
    path(
        "agents/<uuid:agent_id>/sessions/heartbeat/",
        NativeAppAgentSessionHeartbeatAPIView.as_view(),
        name="agent_session_heartbeat",
    ),
    path(
        "agents/<uuid:agent_id>/sessions/end/",
        NativeAppAgentSessionEndAPIView.as_view(),
        name="agent_session_end",
    ),
]
