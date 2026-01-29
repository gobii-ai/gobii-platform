from rest_framework import status, viewsets, serializers, mixins
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.http import HttpResponseRedirect, Http404
from django.views import View
from django.db import models

from observability import traced, dict_to_attributes
from util.constants.task_constants import TASKS_UNLIMITED
from .agent.tools.sms_sender import ensure_scheme
from .models import (
    ApiKey,
    BrowserUseAgent,
    BrowserUseAgentTask,
    BrowserUseAgentTaskStep,
    LinkShortener,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    CommsChannel,
)
from .serializers import (
    BrowserUseAgentSerializer,
    BrowserUseAgentListSerializer,
    BrowserUseAgentTaskSerializer,
    BrowserUseAgentTaskListSerializer,
    PersistentAgentSerializer,
    PersistentAgentListSerializer,
)
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from .tasks import process_browser_use_task
from .services.task_webhooks import trigger_task_webhook
from .services.persistent_agents import maybe_sync_agent_email_display_name
from opentelemetry import baggage, context, trace
from tasks.services import TaskCreditService
import logging


from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from console.agent_chat.timeline import (
    DEFAULT_PAGE_SIZE as TIMELINE_DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE as TIMELINE_MAX_PAGE_SIZE,
    build_processing_snapshot,
    fetch_timeline_window,
    serialize_message_event,
    serialize_processing_snapshot,
)
from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.agent.core.schedule_parser import ScheduleParser
from agents.services import PretrainedWorkerTemplateService
# Import extend_schema from drf-spectacular with minimal dependencies
from drf_spectacular.utils import extend_schema, extend_schema_view, inline_serializer

logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')


# Standard Pagination (can be customized or moved to settings)
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class PersistentAgentMessageCreateSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=[(choice.value, choice.label) for choice in CommsChannel])
    sender = serializers.CharField(max_length=512)
    recipient = serializers.CharField(max_length=512, required=False, allow_blank=True, allow_null=True)
    subject = serializers.CharField(max_length=512, required=False, allow_blank=True, allow_null=True)
    body = serializers.CharField()
    metadata = serializers.DictField(required=False)


class PersistentAgentSchedulePreviewSerializer(serializers.Serializer):
    schedule = serializers.CharField(allow_blank=True)

@extend_schema_view(
    list=extend_schema(operation_id='listAgents', tags=['browser-use']),
    create=extend_schema(operation_id='createAgent', tags=['browser-use']),
    retrieve=extend_schema(operation_id='getAgent', tags=['browser-use']),
    update=extend_schema(operation_id='updateAgent', tags=['browser-use']),
    # partial_update will also be inferred correctly if it uses the same serializer
    destroy=extend_schema(operation_id='deleteAgent', tags=['browser-use'])
)
class BrowserUseAgentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing BrowserUseAgents.
    """
    queryset = BrowserUseAgent.objects.select_related('persistent_agent')
    serializer_class = BrowserUseAgentSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def _request_organization(self):
        auth = getattr(self.request, 'auth', None)
        if isinstance(auth, ApiKey) and getattr(auth, 'organization_id', None):
            return auth.organization
        return None

    def get_queryset(self):
        """Return BrowserUseAgent instances owned by the user or organization."""
        org = self._request_organization()
        properties = {}

        if org is not None:
            properties['owner_type'] = 'organization'
            properties['organization_id'] = str(org.id)

            Analytics.track_event(
                user_id=self.request.user.id,
                event=AnalyticsEvent.AGENTS_LISTED,
                source=AnalyticsSource.API,
                properties=properties,
            )

            return self.queryset.filter(persistent_agent__organization=org)

        Analytics.track_event(
            user_id=self.request.user.id,
            event=AnalyticsEvent.AGENTS_LISTED,
            source=AnalyticsSource.API,
        )
        return self.queryset.filter(user=self.request.user)

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return BrowserUseAgentListSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        """Associate the agent with the current user"""
        if self._request_organization() is not None:
            raise DRFValidationError(detail="Organization API keys cannot create browser agents.")

        try:
            serializer.save(user=self.request.user)
            Analytics.track_event(user_id=self.request.user.id, event=AnalyticsEvent.AGENT_CREATED, source=AnalyticsSource.API)
        except DjangoValidationError as e:
            raise DRFValidationError(detail=e.message_dict if hasattr(e, 'message_dict') else e.messages)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@extend_schema(operation_id='ping', tags=['utils'], responses={200: serializers.DictField})
def ping(request):
    """Test API connectivity with a simple ping endpoint"""
    Analytics.track_event(user_id=request.user.id, event=AnalyticsEvent.PING, source=AnalyticsSource.API)
    return Response({"pong": True, "user": request.user.email})


@extend_schema_view(
    list=extend_schema(operation_id='listTasks', tags=['browser-use']),
    create=extend_schema(
        operation_id='assignTask',
        tags=['browser-use'],
        responses={
            201: BrowserUseAgentTaskSerializer,
            402: inline_serializer(
                name='InsufficientCreditsResponse',
                fields={
                    'message': serializers.CharField()
                }
            ),
            400: inline_serializer(
                name='ValidationErrorResponse',
                fields={
                    'detail': serializers.CharField()
                }
            )
        }
    ),
    retrieve=extend_schema(operation_id='getTask', tags=['browser-use']),
    update=extend_schema(operation_id='updateTask', tags=['browser-use']),
    destroy=extend_schema(operation_id='deleteTask', tags=['browser-use'])
)
class BrowserUseAgentTaskViewSet(mixins.CreateModelMixin,
                          mixins.RetrieveModelMixin,
                          mixins.UpdateModelMixin,
                          mixins.DestroyModelMixin,
                          mixins.ListModelMixin,
                          viewsets.GenericViewSet):
    """
    ViewSet for managing BrowserUseAgentTasks.
    Supports both agent-specific and user-wide task operations.
    """
    serializer_class = BrowserUseAgentTaskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    lookup_field = 'id'

    def _request_organization(self):
        auth = getattr(self.request, 'auth', None)
        if isinstance(auth, ApiKey) and getattr(auth, 'organization_id', None):
            return auth.organization
        return None

    def _validate_agent_access(self, agent):
        org = self._request_organization()
        if org is not None:
            persistent = getattr(agent, 'persistent_agent', None)
            if not persistent or persistent.organization_id != org.id:
                raise Http404
            return

        if agent.user != self.request.user:
            raise Http404

    def get_serializer_class(self, action=None):
        current_action = action or self.action
        if current_action in ['list', 'list_all']:
            return BrowserUseAgentTaskListSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        """Return tasks owned by the user. If an agentId path parameter is present,
        filter to that agent only.  Includes agent-less tasks when listing at the
        user level.
        """
        with traced("GET Tasks Queryset") as span:
            # Note: We've had a bunch of issues with this not detecting authenticated or not correctly; this try/except
            # seems a bit egregious but should help us from clogging up error logs with user ID issues.
            try:
                if self.request.user.is_authenticated:
                    span.set_attribute('user.id', str(self.request.user.id))
                else:
                    span.set_attribute('user.id', '0')
            except Exception as e:
                logger.info(f"Could not set user ID in span: {str(e)}")
                span.set_attribute('user.id', '0')


            qs = BrowserUseAgentTask.objects.alive().select_related('agent', 'agent__persistent_agent')

            org = self._request_organization()
            if org is not None:
                span.set_attribute('tasks.owner_type', 'organization')
                span.set_attribute('tasks.organization_id', str(org.id))
                qs = qs.filter(
                    models.Q(organization=org) |
                    models.Q(agent__persistent_agent__organization=org)
                ).distinct()
            else:
                qs = qs.filter(user=self.request.user, organization__isnull=True)

        agentId = self.kwargs.get('agentId')
        properties = {}
        org = self._request_organization()
        if org is not None:
            properties['owner_type'] = 'organization'
            properties['organization_id'] = str(org.id)

        if agentId:
            properties['agent_id'] = str(agentId)

        if agentId:
            # Validate that the referenced agent belongs to the user; 404 otherwise
            with traced("DB-GET Agent", agent_id=str(agentId), user_id=self.request.user.id) as span:
                agent = get_object_or_404(BrowserUseAgent, id=agentId)
                self._validate_agent_access(agent)
                qs = qs.filter(agent_id=agentId)
            Analytics.track_event(user_id=self.request.user.id, event=AnalyticsEvent.TASKS_LISTED, source=AnalyticsSource.API, properties=properties)

        return qs

    @extend_schema(operation_id='listAllTasks', tags=['browser-use'])
    @action(detail=False, methods=['get'])
    def list_all(self, request):
        with traced("GET tasks", user_id=self.request.user.id) as span:
            org = self._request_organization()
            queryset = BrowserUseAgentTask.objects.alive().select_related('agent', 'agent__persistent_agent')

            if org is not None:
                span.set_attribute('tasks.owner_type', 'organization')
                span.set_attribute('tasks.organization_id', str(org.id))
                queryset = queryset.filter(
                    models.Q(organization=org) |
                    models.Q(agent__persistent_agent__organization=org)
                ).distinct()
            else:
                queryset = queryset.filter(user=request.user, organization__isnull=True)

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = self.get_serializer(queryset, many=True)
            properties = {}
            if org is not None:
                properties['owner_type'] = 'organization'
                properties['organization_id'] = str(org.id)
            Analytics.track_event(user_id=request.user.id, event=AnalyticsEvent.TASKS_LISTED, source=AnalyticsSource.API, properties=properties or None)
        return Response(serializer.data)

    def perform_create(self, serializer):
        """Create a task; works for both agent-scoped and agent-less routes."""
        agentId = self.kwargs.get('agentId')

        with traced("POST task", user_id=self.request.user.id) as span:
            span.set_attribute('agent.id', str(agentId) if agentId else '')  # Set agent ID if available

            agent = None

            if agentId:
                # Agent-scoped route – trust the path parameter
                agent = get_object_or_404(BrowserUseAgent, id=agentId)
                self._validate_agent_access(agent)
            else:
                # User-level route – optional JSON field
                agent = serializer.validated_data.get('agent')
                if agent is not None:
                    self._validate_agent_access(agent)

            org = self._request_organization()

            wait_time = serializer.validated_data.pop('wait', None)

            # Extract secrets before saving
            secrets = serializer.validated_data.pop('secrets', None)

            try:
                save_kwargs = {'agent': agent, 'user': self.request.user}
                if org is not None:
                    save_kwargs['organization'] = org
                elif agent and hasattr(agent, 'persistent_agent') and getattr(agent.persistent_agent, 'organization', None):
                    save_kwargs['organization'] = agent.persistent_agent.organization

                task = serializer.save(**save_kwargs)

                ctx = baggage.set_baggage("task.id", str(task.id), context.get_current())
                context.attach(ctx)

                # Handle secrets encryption if provided
                if secrets:
                    try:
                        from .encryption import SecretsEncryption
                        task.encrypted_secrets = SecretsEncryption.encrypt_secrets(secrets, allow_legacy=False)
                        task.secret_keys = SecretsEncryption.get_secret_keys_for_audit(secrets)
                        task.save(update_fields=['encrypted_secrets', 'secret_keys'])

                        # Log secret usage (keys only, never values)
                        logger.info(
                            "Task %s created with secrets",
                            task.id,
                            extra={
                                'task_id': str(task.id),
                                'user_id': task.user_id,
                                'secret_keys': task.secret_keys,
                                'agent_id': str(task.agent_id) if task.agent else None
                            }
                        )
                    except Exception as e:
                        # If encryption fails, delete the task and raise error
                        task.delete()
                        logger.error(f"Failed to encrypt secrets for task: {str(e)}")
                        raise DRFValidationError(detail="Failed to process secrets securely")

                # Get the current data from the serializer
                task_data = serializer.data

                # Store data for later enhancement with wait results
                self.wait_result_data = None

                span.set_attribute('task.wait_time', task.status)

                if wait_time is not None:
                    # Send to celery & optionally wait
                    with traced("WAIT task.complete", wait_time=wait_time) as span:
                        span.add_event('TASK Started', {'task.id': str(task.id), 'agent.id': str(task.agent.id) if task.agent else ''})
                        async_result = process_browser_use_task.apply_async(args=[str(task.id)])

                        try:
                            # Wait for the result with the specified timeout
                            async_result.wait(timeout=wait_time)

                            # Check if the task completed within the wait time
                            if async_result.ready():
                                # Task completed, get the updated task
                                with traced("DB-REFRESH task") as span:
                                    task.refresh_from_db()

                                # Prepare result data dictionary
                                wait_result = {
                                    'id': str(task.id),
                                    'agent_id': str(task.agent.id) if task.agent else None,
                                }

                                if task.status == BrowserUseAgentTask.StatusChoices.COMPLETED:
                                    # Find the result step
                                    result_step = BrowserUseAgentTaskStep.objects.filter(
                                        task=task, is_result=True
                                    ).first()

                                    if result_step:
                                        # Since result_value is now a JSONField, it comes back as a Python object
                                        # directly from the database, so we can use it as is.
                                        # No need for json.loads or json.dumps here - DRF will handle the serialization
                                        wait_result['result'] = result_step.result_value
                                        wait_result['status'] = 'completed'
                                        span.add_event('TASK Completed', {'task.id': str(task.id), 'agent.id': str(task.agent.id) if task.agent else ''})

                                elif task.status == BrowserUseAgentTask.StatusChoices.FAILED:
                                    # Add error message to the wait_result dict
                                    wait_result['status'] = 'failed'
                                    wait_result['error_message'] = task.error_message
                                    span.add_event('TASK Failed', {'task.id': str(task.id), 'agent.id': str(task.agent.id) if task.agent else ''})

                                else:
                                    wait_result['status'] = task.status
                                    span.add_event('TASK Wait Time Exceeded', {'task.id': str(task.id), 'agent.id': str(task.agent.id) if task.agent else ''})

                                # Store for create() to use
                                self.wait_result_data = wait_result

                            else:
                                # Task is still running
                                self.wait_result_data = {
                                    'status': 'in_progress',
                                    'id': str(task.id),
                                    'agent_id': str(task.agent.id) if task.agent else None,
                                }

                        except Exception as e:
                            # If wait timeout or any other error, task continues in background
                            self.wait_result_data = {
                                'status': 'in_progress',
                                'wait_error': str(e),
                                'id': str(task.id),
                                'agent_id': str(task.agent.id) if task.agent else None,
                            }
                else:
                    # Original behavior - async task
                    with traced("ASYNC task") as span:
                        # Send to celery without waiting
                        process_browser_use_task.delay(str(task.id))

                # Calculate duration from task creation to last step update
                duration = None
                isAsync = wait_time is None

                if not isAsync:
                    task_step = BrowserUseAgentTaskStep.objects.filter(task=task).last()
                    if task_step and task_step.updated_at:
                        duration = (task_step.updated_at - task.created_at).total_seconds()

                properties = {
                    'agent_id': str(task.agent.id) if task.agent else None,
                    'task_id': str(task.id),
                    'ip': 0, # this is coming from the server, not the user, so 0 means ignore the ip - not relevant
                    'task': {
                      'prompt': task_data.get('prompt'),
                      'uses_schema': task_data.get('output_schema') is not None,
                      'output_schema': task_data.get('output_schema') if task_data.get('output_schema') else None,
                      'wait': wait_time,
                      'error_message': task_data.get('error_message') if task_data.get('error_message') else None,
                      'status': task.status,
                      'created_at': task.created_at,
                      'updated_at': task.updated_at,
                      'is_deleted': task.is_deleted,
                      'deleted_at': task.deleted_at,
                      'async': isAsync,
                      'duration': duration,
                    }
                }

                attr_for_span = dict_to_attributes(properties["task"], 'task')
                span.set_attributes(attr_for_span)

                # Track task creation
                org = getattr(task, "organization", None)
                properties = Analytics.with_org_properties(properties, organization=org)
                Analytics.track_event(
                    user_id=task.user_id,
                    event=AnalyticsEvent.TASK_CREATED,
                    source=AnalyticsSource.API,
                    properties=properties.copy(),
                    ip="0"
                )
                if properties.get('organization'):
                    Analytics.track_event(
                        user_id=task.user_id,
                        event=AnalyticsEvent.ORGANIZATION_TASK_CREATED,
                        source=AnalyticsSource.API,
                        properties=properties.copy(),
                        ip="0"
                    )

            except DjangoValidationError as e:
                raise DRFValidationError(detail=e.message_dict if hasattr(e, 'message_dict') else e.messages)
            except Exception as e:
                raise DRFValidationError(detail=str(e))

    def perform_update(self, serializer):
        serializer.save()
        
    def create(self, request, *args, **kwargs):
        """Override create to handle wait parameter results."""

        # Check if the user has enough task credits
        available = TaskCreditService.calculate_available_tasks(request.user)
        if available <= 0 and available != TASKS_UNLIMITED:
            message = "User does not have enough task credits to create a new task."
            if settings.GOBII_PROPRIETARY_MODE:
                message = (
                    "User does not have enough task credits to create a new task. "
                    "Please upgrade your plan or enable extra task purchases."
                )
            return Response({
                    "message": message,
                },
                status=status.HTTP_402_PAYMENT_REQUIRED
            )


        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        
        # If we have wait results, merge them with the serializer data
        if hasattr(self, 'wait_result_data') and self.wait_result_data:
            response_data = self.wait_result_data
            
            # Include other fields from serializer data that weren't in wait_result_data
            for key, value in serializer.data.items():
                if key not in response_data:
                    response_data[key] = value
                    
            return Response(response_data, status=status.HTTP_201_CREATED, headers=headers)
        
        # Regular response without wait results
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_destroy(self, instance):
        with traced("TASK Delete") as span:
            instance.is_deleted = True
            instance.deleted_at = timezone.now()
            instance.save(update_fields=['is_deleted', 'deleted_at'])
            span.add_event('TASK Deleted', {'task.id': str(instance.id), 'agent.id': str(instance.agent.id) if instance.agent else None})
            org = getattr(instance, "organization", None)
            props = Analytics.with_org_properties(
                {
                    'agent_id': str(instance.agent.id) if instance.agent else None,
                    'task_id': str(instance.id),
                },
                organization=org,
            )
            Analytics.track_event(
                user_id=instance.user_id,
                event=AnalyticsEvent.TASK_DELETED,
                source=AnalyticsSource.API,
                properties=props.copy(),
            )
            if props.get('organization'):
                Analytics.track_event(
                    user_id=instance.user_id,
                    event=AnalyticsEvent.ORGANIZATION_TASK_DELETED,
                    source=AnalyticsSource.API,
                    properties=props.copy(),
                )

    @extend_schema(operation_id='getTaskResult', tags=['browser-use'], responses=BrowserUseAgentTaskSerializer)
    @action(detail=True, methods=['get'])
    def result(self, request, id=None, agentId=None):
        task = self.get_object()
        with traced("GET Task Result", user_id=task.user_id) as span:
            baggage.set_baggage("task.id", str(task.id), context.get_current())
            span.set_attribute('task.id', str(task.id))

            response_data = {
                "id": str(task.id),
                "agent_id": str(task.agent.id) if task.agent else None,
                "status": task.status,
            }

            span.set_attributes(dict_to_attributes(task, 'task'))

            view_props = Analytics.with_org_properties({}, organization=getattr(task, "organization", None))
            Analytics.track_event(
                user_id=task.user_id,
                event=AnalyticsEvent.TASK_RESULT_VIEWED,
                source=AnalyticsSource.API,
                properties=view_props.copy(),
            )
            if task.status == BrowserUseAgentTask.StatusChoices.COMPLETED:
                with traced("DB-FETCH Task Steps"):
                    result_step = BrowserUseAgentTaskStep.objects.filter(task=task, is_result=True).first()
                    if result_step:
                        # Since result_value is now a JSONField, it comes back as a Python object
                        # No need to parse/stringify as DRF serializes it correctly
                        response_data["result"] = result_step.result_value
                    else:
                        response_data["result"] = None
                        response_data["message"] = "Result not found for completed task."
            elif task.status == BrowserUseAgentTask.StatusChoices.FAILED:
                response_data["result"] = None
                if task.error_message:
                    response_data["error_message"] = task.error_message
            elif task.status in [BrowserUseAgentTask.StatusChoices.PENDING, BrowserUseAgentTask.StatusChoices.IN_PROGRESS]:
                 response_data["message"] = "Task is not yet completed."
            return Response(response_data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id='cancelTask',
        tags=['browser-use'],
        request=None,
        responses={
            200: inline_serializer(
                name='CancelTaskResponse',
                fields={
                    'status': serializers.CharField(),
                    'message': serializers.CharField()
                }
            ),
            409: inline_serializer(
                name='CancelTaskConflictResponse',
                fields={'detail': serializers.CharField()}
            )
        }
    )
    @action(detail=True, methods=['post'])
    def cancel(self, request, id=None, agentId=None):
        task = self.get_object()
        with traced("POST Cancel Task", user_id=task.user_id) as span:
            span.set_attribute('task.id', str(task.id))
            span.set_attribute('agent.id', str(agentId))
            if task.status in [BrowserUseAgentTask.StatusChoices.PENDING, BrowserUseAgentTask.StatusChoices.IN_PROGRESS]:
                task.status = BrowserUseAgentTask.StatusChoices.CANCELLED
                task.updated_at = timezone.now()
                with traced("DB-UPDATE Task"):
                    task.save(update_fields=['status', 'updated_at'])
                    span.add_event('TASK Cancelled', {'agent.id': str(agentId)})

                try:
                    trigger_task_webhook(task)
                except Exception:
                    logger.exception("Unexpected error while triggering webhook for cancelled task %s", task.id)

                cancel_props = Analytics.with_org_properties(
                    {
                        'task_id': str(task.id),
                        'agent_id': str(agentId),
                    },
                    organization=getattr(task, "organization", None),
                )
                Analytics.track_event(
                    user_id=task.user_id,
                    event=AnalyticsEvent.TASK_CANCELLED,
                    source=AnalyticsSource.API,
                    properties=cancel_props.copy(),
                )

                return Response({'status': 'cancelled', 'message': 'Task has been cancelled.'}, status=status.HTTP_200_OK)
            else:
                return Response(
                    {'detail': f'Task is already {task.status} and cannot be cancelled.'},
                    status=status.HTTP_409_CONFLICT
                )


@extend_schema_view(
    list=extend_schema(operation_id='listPersistentAgents', tags=['persistent-agents']),
    retrieve=extend_schema(operation_id='getPersistentAgent', tags=['persistent-agents']),
    create=extend_schema(operation_id='createPersistentAgent', tags=['persistent-agents']),
    update=extend_schema(operation_id='updatePersistentAgent', tags=['persistent-agents']),
    partial_update=extend_schema(operation_id='partialUpdatePersistentAgent', tags=['persistent-agents']),
    destroy=extend_schema(operation_id='deletePersistentAgent', tags=['persistent-agents'])
)
class PersistentAgentViewSet(viewsets.ModelViewSet):
    queryset = PersistentAgent.objects.non_eval().select_related('browser_use_agent', 'organization', 'preferred_contact_endpoint')
    serializer_class = PersistentAgentSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    lookup_field = 'id'

    def _request_organization(self):
        auth = getattr(self.request, 'auth', None)
        if isinstance(auth, ApiKey) and getattr(auth, 'organization_id', None):
            return auth.organization
        return None

    def _track_agent_event(self, agent: PersistentAgent, event: str, *, organization_event: str | None = None, extra: dict | None = None) -> None:
        props = {
            'agent_id': str(agent.id),
            'agent_name': agent.name,
        }
        org = self._request_organization()
        if org is not None:
            props['owner_type'] = 'organization'
            props['organization_id'] = str(org.id)
        if extra:
            props.update(extra)

        Analytics.track_event(
            user_id=self.request.user.id,
            event=event,
            source=AnalyticsSource.API,
            properties=props.copy(),
        )
        if organization_event and org is not None:
            Analytics.track_event(
                user_id=self.request.user.id,
                event=organization_event,
                source=AnalyticsSource.API,
                properties=props.copy(),
            )

    def get_queryset(self):
        org = self._request_organization()
        if org is not None:
            return self.queryset.filter(organization=org)
        return self.queryset.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == 'list':
            return PersistentAgentListSerializer
        return super().get_serializer_class()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['organization'] = self._request_organization()
        return context

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        props = {}
        org = self._request_organization()
        if org is not None:
            props['owner_type'] = 'organization'
            props['organization_id'] = str(org.id)
        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENTS_LISTED,
            source=AnalyticsSource.API,
            properties=props or None,
        )
        return response

    def retrieve(self, request, *args, **kwargs):
        agent = self.get_object()
        serializer = self.get_serializer(agent)
        self._track_agent_event(agent, AnalyticsEvent.PERSISTENT_AGENT_VIEWED)
        return Response(serializer.data)

    def perform_create(self, serializer):
        agent = serializer.save()
        self._track_agent_event(
            agent,
            AnalyticsEvent.PERSISTENT_AGENT_CREATED,
            organization_event=AnalyticsEvent.ORGANIZATION_PERSISTENT_AGENT_CREATED,
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        prev_name = instance.name if instance else None
        agent = serializer.save()
        if agent.name != prev_name:
            maybe_sync_agent_email_display_name(agent, previous_name=prev_name)
        self._track_agent_event(agent, AnalyticsEvent.PERSISTENT_AGENT_UPDATED)

    def destroy(self, request, *args, **kwargs):
        agent = self.get_object()
        agent.is_active = False
        agent.life_state = PersistentAgent.LifeState.EXPIRED
        agent.schedule = None
        agent.save(update_fields=['is_active', 'life_state', 'schedule'])
        self._track_agent_event(
            agent,
            AnalyticsEvent.PERSISTENT_AGENT_DELETED,
            organization_event=AnalyticsEvent.ORGANIZATION_PERSISTENT_AGENT_DELETED,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _resolve_agent_recipient(self, agent: PersistentAgent, channel: CommsChannel, explicit: str | None) -> str:
        if explicit:
            return explicit

        endpoint = (
            PersistentAgentCommsEndpoint.objects.filter(owner_agent=agent, channel=channel.value, is_primary=True).first()
            or PersistentAgentCommsEndpoint.objects.filter(owner_agent=agent, channel=channel.value).order_by('-is_primary', 'created_at').first()
        )
        if endpoint is None:
            raise DRFValidationError({'recipient': [f'Agent has no {channel.value} endpoint.']})
        return endpoint.address

    @action(detail=True, methods=['get'], url_path='timeline')
    def timeline(self, request, id=None):
        agent = self.get_object()
        direction = (request.query_params.get('direction') or 'initial').lower()
        if direction not in {'initial', 'older', 'newer'}:
            raise DRFValidationError({'direction': ['Invalid direction parameter.']})

        cursor = request.query_params.get('cursor') or None
        try:
            limit = int(request.query_params.get('limit', TIMELINE_DEFAULT_PAGE_SIZE))
        except ValueError:
            raise DRFValidationError({'limit': ['limit must be an integer']})
        limit = max(1, min(limit, TIMELINE_MAX_PAGE_SIZE))

        window = fetch_timeline_window(agent, cursor=cursor, direction=direction, limit=limit)
        payload = {
            'events': window.events,
            'oldest_cursor': window.oldest_cursor,
            'newest_cursor': window.newest_cursor,
            'has_more_older': window.has_more_older,
            'has_more_newer': window.has_more_newer,
            'processing_active': window.processing_active,
            'processing_snapshot': serialize_processing_snapshot(window.processing_snapshot),
        }
        return Response(payload)

    @action(detail=True, methods=['post'], url_path='messages')
    def create_message(self, request, id=None):
        agent = self.get_object()
        serializer = PersistentAgentMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        try:
            channel = CommsChannel(payload['channel'])
        except ValueError as exc:
            raise DRFValidationError({'channel': [str(exc)]})

        sender = payload['sender']
        recipient = self._resolve_agent_recipient(agent, channel, payload.get('recipient'))

        if not agent.is_sender_whitelisted(channel, sender):
            return Response({'detail': 'Sender is not allowed to message this agent.'}, status=status.HTTP_403_FORBIDDEN)

        raw_payload = {
            'source': 'persistent-agent-api',
            'api_user_id': request.user.id,
            'metadata': payload.get('metadata') or {},
        }
        parsed = ParsedMessage(
            sender=sender,
            recipient=recipient,
            subject=payload.get('subject'),
            body=payload['body'],
            attachments=[],
            raw_payload=raw_payload,
            msg_channel=channel.value,
        )

        info = ingest_inbound_message(channel, parsed)
        event = serialize_message_event(info.message)

        channel_event_map = {
            CommsChannel.EMAIL: AnalyticsEvent.PERSISTENT_AGENT_EMAIL_RECEIVED,
            CommsChannel.SMS: AnalyticsEvent.PERSISTENT_AGENT_SMS_RECEIVED,
        }
        channel_event = channel_event_map.get(channel)
        if channel_event:
            self._track_agent_event(agent, channel_event)

        return Response({'event': event}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='processing-status')
    def processing_status(self, request, id=None):
        agent = self.get_object()
        snapshot = build_processing_snapshot(agent)
        return Response({
            'processing_active': snapshot.active,
            'processing_snapshot': serialize_processing_snapshot(snapshot),
        })

    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, id=None):
        agent = self.get_object()
        updates: set[str] = set()
        if not agent.is_active:
            agent.is_active = True
            updates.add('is_active')
        if agent.life_state != PersistentAgent.LifeState.ACTIVE:
            agent.life_state = PersistentAgent.LifeState.ACTIVE
            updates.add('life_state')
        if updates:
            agent.save(update_fields=list(updates))
            self._track_agent_event(agent, AnalyticsEvent.PERSISTENT_AGENT_UPDATED)
        return Response({'status': 'activated', 'updated': bool(updates)})

    @action(detail=True, methods=['post'], url_path='deactivate')
    def deactivate(self, request, id=None):
        agent = self.get_object()
        updates = {}
        if agent.is_active:
            agent.is_active = False
            updates['is_active'] = False
        if updates:
            agent.save(update_fields=list(updates.keys()))
            self._track_agent_event(agent, AnalyticsEvent.PERSISTENT_AGENT_UPDATED)
        return Response({'status': 'deactivated', 'updated': bool(updates)})

    @action(detail=True, methods=['post'], url_path='schedule/preview')
    def schedule_preview(self, request, id=None):
        self.get_object()
        serializer = PersistentAgentSchedulePreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        schedule = (serializer.validated_data.get('schedule') or '').strip()

        if not schedule:
            return Response({'valid': True, 'disabled': True, 'description': None})

        try:
            ScheduleParser.parse(schedule)
        except ValueError as exc:
            raise DRFValidationError({'schedule': [str(exc)]})

        description = PretrainedWorkerTemplateService.describe_schedule(schedule)
        return Response({'valid': True, 'disabled': False, 'description': description})

    @action(detail=True, methods=['get'], url_path='web-tasks')
    def web_tasks(self, request, id=None):
        agent = self.get_object()
        browser_agent = agent.browser_use_agent
        if browser_agent is None:
            return Response({'results': [], 'limit': 0})

        status_filter = request.query_params.get('status')
        if status_filter:
            valid_statuses = set(BrowserUseAgentTask.StatusChoices.values)
            if status_filter not in valid_statuses:
                raise DRFValidationError({'status': ['Invalid status filter.']})

        try:
            limit_param = request.query_params.get('limit')
            limit = int(limit_param) if limit_param else 50
        except ValueError:
            raise DRFValidationError({'limit': ['limit must be an integer']})
        limit = max(1, min(limit, 200))

        tasks_qs = BrowserUseAgentTask.objects.alive().filter(agent=browser_agent).order_by('-created_at')
        if status_filter:
            tasks_qs = tasks_qs.filter(status=status_filter)

        tasks = list(tasks_qs[:limit])
        serializer = BrowserUseAgentTaskListSerializer(tasks, many=True)
        return Response({'results': serializer.data, 'limit': limit})


class LinkShortenerRedirectView(View):
    """Redirect from a short code to the stored URL."""

    @tracer.start_as_current_span('LINK SHORTENER Redirect')
    def get(self, request, code):
        trace.get_current_span().set_attribute('link_code', code)
        link = get_object_or_404(LinkShortener, code=code)

        # We would've 404 if the link was not found, so we can assume it exists.
        url = ensure_scheme(link.url)

        if request.user.is_authenticated:
            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.SMS_SHORTENED_LINK_CLICKED,
                source=AnalyticsSource.SMS,
                properties={
                    'link_code': link.code,
                    'link_original_url': link.url,
                    'link_shortened_url': url,
                }
            )

        link.increment_hits()
        return HttpResponseRedirect(url)


class PipedreamConnectRedirectView(View):
    """
    Just-in-time Pipedream connect link generator.

    Generates a fresh Pipedream connect link on each request and redirects the user,
    avoiding the 4-hour expiration issue with pre-generated links.
    """

    @tracer.start_as_current_span('PIPEDREAM JIT Connect')
    def get(self, request, agent_id, app_slug):
        from django.conf import settings
        from django.contrib.auth.views import redirect_to_login
        from django.template.response import TemplateResponse
        from api.integrations.pipedream_connect import create_connect_session

        span = trace.get_current_span()
        span.set_attribute('agent_id', str(agent_id))
        span.set_attribute('app_slug', app_slug)

        # Redirect unauthenticated users to login (with next= to return here after)
        if not request.user.is_authenticated:
            logger.info("PD JIT Connect: redirecting to login agent=%s", agent_id)
            return redirect_to_login(
                next=request.get_full_path(),
                login_url=settings.LOGIN_URL,
            )

        # Check if Pipedream integration is configured
        if not all([
            getattr(settings, 'PIPEDREAM_CLIENT_ID', ''),
            getattr(settings, 'PIPEDREAM_CLIENT_SECRET', ''),
            getattr(settings, 'PIPEDREAM_PROJECT_ID', ''),
        ]):
            logger.warning("PD JIT Connect: Pipedream not configured")
            return TemplateResponse(
                request,
                "integrations/pipedream_connect_error.html",
                context={
                    'title': 'Integration Not Available',
                    'heading': 'Integration not available',
                    'icon_type': 'info',
                    'message': 'Third-party integrations are not configured on this Gobii instance.',
                    'message_secondary': 'If you are self-hosting, please configure the Pipedream environment variables (PIPEDREAM_CLIENT_ID, PIPEDREAM_CLIENT_SECRET, PIPEDREAM_PROJECT_ID).',
                    'show_retry': False,
                    'show_support': False,
                },
                status=503,
            )

        # Get the agent - redirect to console if not found
        try:
            agent = PersistentAgent.objects.get(id=agent_id)
        except PersistentAgent.DoesNotExist:
            logger.warning("PD JIT Connect: agent not found agent=%s", agent_id)
            return HttpResponseRedirect('/console/')

        # Redirect to console if agent is expired
        if agent.life_state == PersistentAgent.LifeState.EXPIRED:
            logger.info("PD JIT Connect: agent expired agent=%s", agent_id)
            return HttpResponseRedirect('/console/')

        # Check user has access to this agent (owns it or is in the org)
        has_access = (agent.user_id == request.user.id) or (
                agent.organization_id and OrganizationMembership.objects.filter(
                org_id=agent.organization_id,
                user_id=request.user.id,
                status=OrganizationMembership.OrgStatus.ACTIVE
            ).exists()
        )

        if not has_access:
            logger.warning(
                "PD JIT Connect: access denied user=%s agent=%s",
                request.user.id, agent_id
            )
            # Redirect to console rather than 404 for better UX
            return HttpResponseRedirect('/console/')

        # Always create a fresh connect link (Pipedream links are single-use)
        session, connect_url = create_connect_session(agent, app_slug)

        if not connect_url:
            logger.error(
                "PD JIT Connect: failed to generate link agent=%s app=%s session=%s",
                agent_id, app_slug, session.id
            )
            return TemplateResponse(
                request,
                "integrations/pipedream_connect_error.html",
                status=503,
            )

        logger.info(
            "PD JIT Connect: redirecting user=%s agent=%s app=%s session=%s",
            request.user.id, agent_id, app_slug, session.id
        )

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.PIPEDREAM_JIT_CONNECT_REDIRECT,
            source=AnalyticsSource.CONSOLE,
            properties={
                'agent_id': str(agent_id),
                'app_slug': app_slug,
                'session_id': str(session.id),
            }
        )

        return HttpResponseRedirect(connect_url)
