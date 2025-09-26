from rest_framework import status, viewsets, serializers, mixins
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.http import HttpResponseRedirect, Http404
from django.views import View

from observability import traced, dict_to_attributes
from util.constants.task_constants import TASKS_UNLIMITED
from .agent.tools.sms_sender import ensure_scheme
from .models import (
    ApiKey,
    BrowserUseAgent,
    BrowserUseAgentTask,
    BrowserUseAgentTaskStep,
    LinkShortener,
    PersistentAgent,
)
from .serializers import (
    BrowserUseAgentSerializer,
    BrowserUseAgentListSerializer,
    BrowserUseAgentTaskSerializer,
    BrowserUseAgentTaskListSerializer,
)
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from .tasks import process_browser_use_task
from opentelemetry import baggage, context, trace
from tasks.services import TaskCreditService
import logging


from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

# Import extend_schema from drf-spectacular with minimal dependencies
from drf_spectacular.utils import extend_schema, extend_schema_view, inline_serializer

logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')


def _derive_task_organization(task: BrowserUseAgentTask):
    """Return the organization associated with a task if one can be inferred."""
    org = None
    try:
        credit = getattr(task, "task_credit", None)
        if credit is not None and getattr(credit, "organization_id", None):
            org = credit.organization
    except Exception:  # pragma: no cover - defensive fetch guard
        org = None

    if org is not None:
        return org

    agent = getattr(task, "agent", None)
    if agent is None:
        return None

    try:
        persistent = getattr(agent, "persistent_agent", None)
        if persistent is None and isinstance(agent, BrowserUseAgent):
            persistent = PersistentAgent.objects.filter(browser_use_agent=agent).select_related("organization").first()
        if persistent is not None and getattr(persistent, "organization_id", None):
            return persistent.organization
    except PersistentAgent.DoesNotExist:  # pragma: no cover - safe fallback
        return None
    except Exception:  # pragma: no cover - defensive fallback
        return None

    return None

# Standard Pagination (can be customized or moved to settings)
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

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
    queryset = BrowserUseAgent.objects.all()
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
                qs = qs.filter(agent__persistent_agent__organization=org)
            else:
                qs = qs.filter(user=self.request.user)

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
                queryset = queryset.filter(agent__persistent_agent__organization=org)
            else:
                queryset = queryset.filter(user=request.user)
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

            if org is not None:
                if agent is None:
                    raise DRFValidationError(detail={'agent': 'Organization API keys must specify an agent.'})

            try:
                task = serializer.save(agent=agent, user=self.request.user)

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
                org = _derive_task_organization(task)
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
            return Response({
                    "message": "User does not have enough task credits to create a new task. Please upgrade your plan or enable extra task purchases.",
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
            org = _derive_task_organization(instance)
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

            view_props = Analytics.with_org_properties({}, organization=_derive_task_organization(task))
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

                cancel_props = Analytics.with_org_properties(
                    {
                        'task_id': str(task.id),
                        'agent_id': str(agentId),
                    },
                    organization=_derive_task_organization(task),
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
