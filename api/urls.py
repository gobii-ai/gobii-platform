from django.urls import path
from rest_framework.routers import SimpleRouter

# Import the viewsets
from .views import (
    ping,
    BrowserUseAgentViewSet,
    BrowserUseAgentTaskViewSet,
    PersistentAgentViewSet,
)
from .webhooks import (
    sms_webhook,
    sms_status_webhook,
    email_webhook_postmark,
    email_webhook_mailgun,
    open_and_link_webhook,
    pipedream_connect_webhook,
)

app_name = "api"

# Simple router for agents
router = SimpleRouter()
# Register the browser-use routes before the generic agents routes so the more
# specific path does not get shadowed by the base "agents/<pk>/" pattern.
router.register(r'agents/browser-use', BrowserUseAgentViewSet, basename='browseruseagent')
router.register(r'agents', PersistentAgentViewSet, basename='persistentagent')

urlpatterns = [
    # Utility endpoints
    path("ping/", ping, name="ping"),
    
    # Include the router URLs for agents
    *router.urls,
    
    # Task endpoints - explicit paths for clean URL naming
    # Agent-specific task endpoints
    path("agents/browser-use/<uuid:agentId>/tasks/", 
         BrowserUseAgentTaskViewSet.as_view({'get': 'list', 'post': 'create'}),
         name="agent-tasks-list"),
         
    path("agents/browser-use/<uuid:agentId>/tasks/<uuid:id>/", 
         BrowserUseAgentTaskViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
         name="agent-tasks-detail"),
         
    path("agents/browser-use/<uuid:agentId>/tasks/<uuid:id>/cancel/", 
         BrowserUseAgentTaskViewSet.as_view({'post': 'cancel'}),
         name="agent-tasks-cancel-task"),
         
    path("agents/browser-use/<uuid:agentId>/tasks/<uuid:id>/result/", 
         BrowserUseAgentTaskViewSet.as_view({'get': 'result'}),
         name="agent-tasks-result"),
         
    # User's global task endpoints (no agent specified)
    path(
        "tasks/browser-use/",
        BrowserUseAgentTaskViewSet.as_view({
            'get': 'list_all',
            'post': 'create',
        }),
        name="user-tasks-list",
    ),
         
    path("tasks/browser-use/<uuid:id>/", 
         BrowserUseAgentTaskViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
         name="user-tasks-detail"),
         
    path("tasks/browser-use/<uuid:id>/cancel/", 
         BrowserUseAgentTaskViewSet.as_view({'post': 'cancel'}),
         name="user-tasks-cancel"),
         
    path("tasks/browser-use/<uuid:id>/result/", 
         BrowserUseAgentTaskViewSet.as_view({'get': 'result'}),
         name="user-tasks-result"),

    #  Webhooks for messages endpoint
    path('webhooks/inbound/sms/', sms_webhook, name='sms_webhook'),
    path('webhooks/status/sms/', sms_status_webhook, name='sms_status_webhook'),
    path('webhooks/inbound/email/', email_webhook_postmark, name='email_webhook'),
    path('webhooks/inbound/email/mg/', email_webhook_mailgun, name='email_webhook_mailgun'),
    # Pipedream Connect webhook (one-time)
    path('webhooks/pipedream/connect/<uuid:session_id>/', pipedream_connect_webhook, name='pipedream_connect_webhook'),

    # Webhook for persistent agent email opens and link clicks business intelligence
    path("webhooks/bi/email/", open_and_link_webhook, name="open_and_link_webhook"),
]
