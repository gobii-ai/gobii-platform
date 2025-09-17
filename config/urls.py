from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.urls.conf import re_path
from drf_spectacular.views import (
    SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
)
from console.views import (
    ConsoleHome,
    ApiKeyListView,
    ApiKeyDetailView,
    ApiKeyTableView,
    ApiKeyBlankFormView,
    ApiKeyCreateModalView,
    BillingView,
    PersistentAgentsView,
    AgentCreateContactView,
    AgentDetailView,
    AgentEnableSmsView,
    AgentDeleteView,
    AgentSecretsView,
    AgentSecretsAddView,
    AgentSecretsAddFormView,
    AgentSecretsEditView,
    AgentSecretsDeleteView,
    ProfileView,
    AgentSecretsRequestView,
    AgentSecretsRequestThanksView,
    AgentSecretRerequestView,
    AgentWelcomeView,
    AgentContactRequestsView,
    AgentContactRequestsThanksView,
    AgentAllowlistView,
    AgentAllowlistInviteAcceptView,
    AgentAllowlistInviteRejectView,
    OrganizationListView,
    OrganizationCreateView,
    OrganizationDetailView,
    OrganizationInviteAcceptView,
    OrganizationInviteRejectView,
    OrganizationInviteRevokeOrgView,
    OrganizationInviteResendOrgView,
    OrganizationMemberRemoveOrgView,
    OrganizationLeaveOrgView,
    OrganizationMemberRoleUpdateOrgView,
    update_billing_settings,
    get_billing_settings,
    cancel_subscription,
    tasks_view,
    task_result_view,
    task_cancel_view,
    task_detail_view,
    grant_credits,
    task_detail_view,
    AgentEmailSettingsView,
)
from console.context_views import SwitchContextView
from pages.views import PaidPlanLanding
from api.views import LinkShortenerRedirectView

urlpatterns = [
    # Pages app includes homepage, health check, and documentation
    path("", include("pages.urls")),

    path("m/<slug:code>/", LinkShortenerRedirectView.as_view(), name="short_link"),

    # Plan landing pages (must be before console to avoid conflict)
    path("plans/<slug:plan>/", PaidPlanLanding.as_view(), name="plan_landing"),
    
    # console
    path("console/", ConsoleHome.as_view(), name="console-home"),
    path("console/switch-context/", SwitchContextView.as_view(), name="switch_context"),
    path("console/api-keys/", ApiKeyListView.as_view(), name="api_keys"),
    path("console/api-keys/blank-form/", ApiKeyBlankFormView.as_view(), name="api_key_blank_form"),
    path("console/api-keys/table/", ApiKeyTableView.as_view(), name="api_keys_table"),
    path("console/api-keys/<uuid:pk>/", ApiKeyDetailView.as_view(), name="api_key_detail"),
    path(
        "console/api-keys/create-modal/",
        ApiKeyCreateModalView.as_view(),
        name="api_key_create_modal",
    ),

    path("console/billing/", BillingView.as_view(), name="billing"),
    path("console/profile/", ProfileView.as_view(), name="profile"),

    path("console/agents/", PersistentAgentsView.as_view(), name="agents"),
    path("console/agents/create/contact/", AgentCreateContactView.as_view(), name="agent_create_contact"),
    path("console/agents/<uuid:pk>/", AgentDetailView.as_view(), name="agent_detail"),
    path("console/agents/<uuid:pk>/welcome/", AgentWelcomeView.as_view(), name="agent_welcome"),
    path("console/agents/<uuid:pk>/enable-sms/", AgentEnableSmsView.as_view(), name="agent_enable_sms"),
    path("console/agents/<uuid:pk>/delete/", AgentDeleteView.as_view(), name="agent_delete"),
    path("console/agents/<uuid:pk>/email/", AgentEmailSettingsView.as_view(), name="agent_email_settings"),
    # Agent secrets management
    path("console/agents/<uuid:pk>/secrets/", AgentSecretsView.as_view(), name="agent_secrets"),
    path("console/agents/<uuid:pk>/secrets/add/", AgentSecretsAddView.as_view(), name="agent_secrets_add"),
    path("console/agents/<uuid:pk>/secrets/add/form/", AgentSecretsAddFormView.as_view(), name="agent_secrets_add_form"),
    path("console/agents/<uuid:pk>/secrets/edit/<uuid:secret_id>/", AgentSecretsEditView.as_view(), name="agent_secrets_edit"),
    path("console/agents/<uuid:pk>/secrets/delete/<uuid:secret_id>/", AgentSecretsDeleteView.as_view(), name="agent_secrets_delete"),
    path("console/agents/<uuid:pk>/secrets/request/", AgentSecretsRequestView.as_view(), name="agent_secrets_request"),
    path("console/agents/<uuid:pk>/secrets/request/thanks/", AgentSecretsRequestThanksView.as_view(), name="agent_secrets_request_thanks"),
    path("console/agents/<uuid:pk>/secrets/request/remove/", AgentSecretsRequestView.as_view(), name="agent_requested_secrets_remove"),
    path("console/agents/<uuid:pk>/secrets/request/remove/<uuid:secret_id>/", AgentSecretsRequestView.as_view(), name="agent_requested_secret_remove"),
    path("console/agents/<uuid:pk>/secrets/rerequest/<uuid:secret_id>/", AgentSecretRerequestView.as_view(), name="agent_secret_rerequest"),
    path("console/agents/<uuid:pk>/contact-requests/", AgentContactRequestsView.as_view(), name="agent_contact_requests"),
    path("console/agents/<uuid:pk>/contact-requests/thanks/", AgentContactRequestsThanksView.as_view(), name="agent_contact_requests_thanks"),
    path("console/agents/<uuid:pk>/allowlist/", AgentAllowlistView.as_view(), name="agent_allowlist"),
    path("console/agent-allowlist-invite/<str:token>/accept/", AgentAllowlistInviteAcceptView.as_view(), name="agent_allowlist_invite_accept"),
    path("console/agent-allowlist-invite/<str:token>/reject/", AgentAllowlistInviteRejectView.as_view(), name="agent_allowlist_invite_reject"),

    path("console/organizations/", OrganizationListView.as_view(), name="organizations"),
    path("console/organizations/add/", OrganizationCreateView.as_view(), name="organization_add"),
    path("console/organizations/<uuid:org_id>/", OrganizationDetailView.as_view(), name="organization_detail"),
    path("console/organizations/invites/<str:token>/accept/", OrganizationInviteAcceptView.as_view(), name="org_invite_accept"),
    path("console/organizations/invites/<str:token>/reject/", OrganizationInviteRejectView.as_view(), name="org_invite_reject"),
    path("console/organizations/<uuid:org_id>/invites/<str:token>/revoke/", OrganizationInviteRevokeOrgView.as_view(), name="org_invite_revoke_org"),
    path("console/organizations/<uuid:org_id>/invites/<str:token>/resend/", OrganizationInviteResendOrgView.as_view(), name="org_invite_resend_org"),
    path("console/organizations/<uuid:org_id>/members/<int:user_id>/remove/", OrganizationMemberRemoveOrgView.as_view(), name="org_member_remove_org"),
    path("console/organizations/<uuid:org_id>/members/<int:user_id>/role/", OrganizationMemberRoleUpdateOrgView.as_view(), name="org_member_role_update_org"),
    path("console/organizations/<uuid:org_id>/leave/", OrganizationLeaveOrgView.as_view(), name="org_leave_org"),

    # Task management views
    path("console/tasks/", tasks_view, name="tasks"),
    # Add these to your urlpatterns in urls.py
    path("console/tasks/<uuid:task_id>/", task_detail_view, name="task_detail"),
    path("console/tasks/<uuid:task_id>/cancel/", task_cancel_view, name="task_cancel"),
    path("console/tasks/<uuid:task_id>/result/", task_result_view, name="task_result"),

    # Admin actions
    path("console/grant-credits/", grant_credits, name="grant_credits"),

    path('billing/settings/update/', update_billing_settings, name='update_billing_settings'),

    path('billing/settings/cancel-subscription/', cancel_subscription, name='cancel_subscription'),

    path('api/v1/user/billing-settings/', get_billing_settings, name='get_billing_settings'),

    # admin & auth
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),

    # API docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema"), name="schema-swagger-ui"),
    path("api/schema/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="schema-redoc"),
    
    # Legacy API docs URL (keeping for backward compatibility)
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api_docs"),

    # API
    path("api/v1/", include("api.urls")),

    # Stripe integration
    path("stripe/", include("djstripe.urls", namespace="djstripe")),
]

# Proprietary-only routes
if settings.GOBII_PROPRIETARY_MODE:
    # Include proprietary URLs at root so names remain 'pages:*' as defined in proprietary/urls.py
    urlpatterns.insert(1, path("", include("proprietary.urls")))

# Serve static files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
