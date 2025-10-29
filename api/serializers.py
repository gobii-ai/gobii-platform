# gobii_platform/api/serializers.py
import uuid
from urllib.parse import urlparse

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from api.agent.short_description import build_listing_description, build_mini_description
from .models import (
    ApiKey,
    BrowserUseAgent,
    BrowserUseAgentTask,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)
from jsonschema import Draft202012Validator, ValidationError as JSValidationError

# Serializer for Listing Agents (id, name, created_at)
class BrowserUseAgentListSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True, format='hex_verbose')
    listing_description = serializers.SerializerMethodField()
    listing_description_source = serializers.SerializerMethodField()
    mini_description = serializers.SerializerMethodField()
    mini_description_source = serializers.SerializerMethodField()

    class Meta:
        model = BrowserUseAgent
        fields = [
            'id',
            'name',
            'created_at',
            'listing_description',
            'listing_description_source',
            'mini_description',
            'mini_description_source',
        ]
        ref_name = "AgentList" # Optional: for explicit component naming

    def _get_listing_tuple(self, obj):
        persistent = getattr(obj, 'persistent_agent', None)
        if not persistent:
            return "Agent is initializing…", "placeholder"
        return build_listing_description(persistent, max_length=200)

    def get_listing_description(self, obj):
        description, _ = self._get_listing_tuple(obj)
        return description

    def get_listing_description_source(self, obj):
        _, source = self._get_listing_tuple(obj)
        return source

    def _get_mini_tuple(self, obj):
        persistent = getattr(obj, 'persistent_agent', None)
        if not persistent:
            return "Agent", "placeholder"
        return build_mini_description(persistent)

    def get_mini_description(self, obj):
        description, _ = self._get_mini_tuple(obj)
        return description

    def get_mini_description_source(self, obj):
        _, source = self._get_mini_tuple(obj)
        return source

class BrowserUseAgentSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True, format='hex_verbose')
    user_email = serializers.ReadOnlyField(source='user.email')
    listing_description = serializers.SerializerMethodField()
    listing_description_source = serializers.SerializerMethodField()
    mini_description = serializers.SerializerMethodField()
    mini_description_source = serializers.SerializerMethodField()

    class Meta:
        model = BrowserUseAgent
        fields = [
            'id',
            'user_email',
            'name',
            'created_at',
            'updated_at',
            'listing_description',
            'listing_description_source',
            'mini_description',
            'mini_description_source',
        ]
        read_only_fields = ('id', 'user_email', 'created_at', 'updated_at') # 'name' is now writable
        ref_name = "AgentDetail" # Optional: for explicit component naming

    def _get_listing_tuple(self, obj):
        persistent = getattr(obj, 'persistent_agent', None)
        if not persistent:
            return "Agent is initializing…", "placeholder"
        return build_listing_description(persistent, max_length=200)

    def get_listing_description(self, obj):
        description, _ = self._get_listing_tuple(obj)
        return description

    def get_listing_description_source(self, obj):
        _, source = self._get_listing_tuple(obj)
        return source

    def _get_mini_tuple(self, obj):
        persistent = getattr(obj, 'persistent_agent', None)
        if not persistent:
            return "Agent", "placeholder"
        return build_mini_description(persistent)

    def get_mini_description(self, obj):
        description, _ = self._get_mini_tuple(obj)
        return description

    def get_mini_description_source(self, obj):
        _, source = self._get_mini_tuple(obj)
        return source

class BrowserUseAgentTaskSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True, format='hex_verbose')
    # Agent may now be supplied (optional) when creating a task via the
    # user-level route.  For agent-scoped routes the view will override it.
    agent = serializers.PrimaryKeyRelatedField(
        queryset=BrowserUseAgent.objects.all(),
        required=False,
        allow_null=True,
        pk_field=serializers.UUIDField(format='hex_verbose'),
    )
    agent_id = serializers.UUIDField(source='agent.id', read_only=True, format='hex_verbose')
    organization_id = serializers.UUIDField(source='organization.id', read_only=True, format='hex_verbose')
    wait = serializers.IntegerField(min_value=0, max_value=1350, required=False, write_only=True)
    secrets = serializers.DictField(
        required=False,
        write_only=True,  # Never return secrets in responses
        help_text="Domain-specific secrets for the task. REQUIRED FORMAT: {'https://example.com': {'x_api_key': 'value', 'x_username': 'user'}}. Each domain can have multiple secrets. Secret keys will be available as placeholders in the prompt for the specified domains."
    )
    credits_cost = serializers.DecimalField(max_digits=12, decimal_places=3, min_value="0.001", required=False, allow_null=True)
    webhook = serializers.URLField(
        source='webhook_url',
        required=False,
        allow_null=True,
        help_text="HTTP or HTTPS URL invoked when the task finishes.",
    )
    webhook_last_called_at = serializers.DateTimeField(read_only=True)
    webhook_last_status_code = serializers.IntegerField(read_only=True, allow_null=True)
    webhook_last_error = serializers.CharField(read_only=True, allow_blank=True, allow_null=True)

    class Meta:
        model = BrowserUseAgentTask
        fields = [
            'id',
            'agent',
            'agent_id',
            'organization_id',
            'prompt',
            'output_schema',
            'status',
            'created_at',
            'updated_at',
            'error_message',
            'wait',
            'secrets',
            'credits_cost',
            'webhook',
            'webhook_last_called_at',
            'webhook_last_status_code',
            'webhook_last_error',
        ]
        read_only_fields = (
            'id',
            'agent_id',
            'organization_id',
            'status',
            'created_at',
            'updated_at',
            'error_message',
            'webhook_last_called_at',
            'webhook_last_status_code',
            'webhook_last_error',
        )
        # 'prompt' and 'output_schema' are writable by not being in read_only_fields
        ref_name = "TaskDetail" # Optional: for explicit component naming

    def validate_prompt(self, value):
        # Accept both strings and dictionaries
        if value is not None and not isinstance(value, (dict, str)):
            raise serializers.ValidationError("prompt must be a string or a valid JSON object.")
        return value

    def validate_webhook(self, value):
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in ('http', 'https'):
            raise serializers.ValidationError("webhook must use http or https.")
        return value
        
    def validate_output_schema(self, value):
        if value is None:
            return value
            
        # Validate the schema against the JSON Schema meta-schema
        try:
            Draft202012Validator.check_schema(value)
        except JSValidationError as exc:
            raise serializers.ValidationError(f"Invalid JSON Schema: {exc.message}")
        except Exception as exc:
            raise serializers.ValidationError(f"Invalid JSON Schema: {str(exc)}")
            
        # Add security checks - no deep nesting, limit property count
        if self._max_depth(value) > 40:
            raise serializers.ValidationError("Schema too deep - maximum nesting level is 40")
        if self._count_props(value) > 2000:
            raise serializers.ValidationError("Schema too complex - maximum property count is 2000")
            
        return value
    
    def validate_secrets(self, value):
        if value is None:
            return value
        
        try:
            from .domain_validation import DomainPatternValidator
            from .encryption import SecretsEncryption
            from constants.security import SecretLimits, ValidationMessages
            
            # Validate size before processing (quick check)
            import json
            serialized_size = len(json.dumps(value).encode('utf-8'))
            if serialized_size > SecretLimits.MAX_TOTAL_SECRETS_SIZE_BYTES:
                raise serializers.ValidationError(ValidationMessages.TOTAL_SECRETS_TOO_LARGE)
            
            # Use the encryption class validation which supports both formats
            # but enforces the new domain-specific format
            SecretsEncryption.validate_and_normalize_secrets(value)
            
            return value
        except ValueError as e:
            raise serializers.ValidationError(str(e))
        except Exception as e:
            raise serializers.ValidationError(f"Invalid secrets format: {str(e)}")
    
    # Helper methods for schema validation
    def _max_depth(self, obj, d=0):
        if isinstance(obj, dict):
            return max([d] + [self._max_depth(v, d + 1) for v in obj.values()])
        if isinstance(obj, list):
            return max([d] + [self._max_depth(v, d + 1) for v in obj])
        return d

    def _count_props(self, obj):
        if isinstance(obj, dict):
            return len(obj) + sum(self._count_props(v) for v in obj.values())
        if isinstance(obj, list):
            return sum(self._count_props(v) for v in obj)
        return 0

    def validate(self, attrs):
        # If this serializer is used for updates, check task status
        if self.instance and self.instance.status != BrowserUseAgentTask.StatusChoices.PENDING:
             # Only allow updates to prompt if the task is PENDING
            if 'prompt' in attrs or 'output_schema' in attrs:
                error_msg = 'Task can be modified only while it is PENDING.'
                raise serializers.ValidationError(
                    {'status': error_msg, 'detail': error_msg}
                )
            # Potentially allow other fields to be updated if necessary, or restrict all updates

        # Creation-time validation: if an agent is provided ensure it belongs to request.user
        request = self.context.get('request')
        if not self.instance and request is not None:
            agent_obj = attrs.get('agent')
            if agent_obj:
                auth = getattr(request, 'auth', None)
                if isinstance(auth, ApiKey) and getattr(auth, 'organization_id', None):
                    persistent = getattr(agent_obj, 'persistent_agent', None)
                    if not persistent or persistent.organization_id != auth.organization_id:
                        raise serializers.ValidationError({'agent': 'Specified agent does not belong to the authenticated organization.'})
                elif agent_obj.user != request.user:
                    raise serializers.ValidationError({'agent': 'Specified agent does not belong to the authenticated user.'})
        return attrs

class BrowserUseAgentTaskListSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True, format='hex_verbose')
    agent_id = serializers.UUIDField(source='agent.id', read_only=True, format='hex_verbose')
    webhook = serializers.URLField(source='webhook_url', read_only=True, allow_null=True)

    class Meta:
        model = BrowserUseAgentTask
        fields = ['id', 'agent_id', 'prompt', 'output_schema', 'status', 'created_at', 'updated_at', 'credits_cost', 'webhook']
        read_only_fields = fields
        ref_name = "TaskList" # Optional: for explicit component naming


class PersistentAgentListSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True, format='hex_verbose')
    user_id = serializers.UUIDField(source='user.id', read_only=True, format='hex_verbose')
    organization_id = serializers.UUIDField(read_only=True, allow_null=True, format='hex_verbose')
    browser_use_agent_id = serializers.UUIDField(source='browser_use_agent.id', read_only=True, format='hex_verbose')
    preferred_contact_endpoint_id = serializers.UUIDField(
        source='preferred_contact_endpoint.id',
        read_only=True,
        allow_null=True,
        format='hex_verbose',
    )

    class Meta:
        model = PersistentAgent
        fields = [
            'id',
            'name',
            'charter',
            'schedule',
            'is_active',
            'life_state',
            'whitelist_policy',
            'last_interaction_at',
            'created_at',
            'updated_at',
            'user_id',
            'organization_id',
            'browser_use_agent_id',
            'preferred_contact_endpoint_id',
            'proactive_opt_in',
            'proactive_last_trigger_at',
        ]
        read_only_fields = fields
        ref_name = "PersistentAgentList"


class PreferredEndpointInputField(serializers.Field):
    default_error_messages = {
        'invalid_choice': 'preferred_contact_endpoint must be "email", "sms", or a valid endpoint id.',
        'does_not_exist': 'Contact endpoint does not exist: {value}.',
    }

    def to_internal_value(self, data):
        if data is None:
            return None
        if isinstance(data, str):
            normalized = data.strip().lower()
            if normalized in {'email', 'sms'}:
                return normalized
            try:
                uuid.UUID(normalized)
            except ValueError:
                self.fail('invalid_choice')
            try:
                return PersistentAgentCommsEndpoint.objects.get(id=normalized)
            except PersistentAgentCommsEndpoint.DoesNotExist:
                self.fail('does_not_exist', value=normalized)
        self.fail('invalid_choice')

    def to_representation(self, value):
        return None


class PersistentAgentSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True, format='hex_verbose')
    user_id = serializers.UUIDField(source='user.id', read_only=True, format='hex_verbose')
    organization_id = serializers.UUIDField(read_only=True, allow_null=True, format='hex_verbose')
    browser_use_agent_id = serializers.UUIDField(source='browser_use_agent.id', read_only=True, format='hex_verbose')
    preferred_contact_endpoint_id = serializers.UUIDField(
        source='preferred_contact_endpoint.id',
        read_only=True,
        allow_null=True,
        format='hex_verbose',
    )
    preferred_contact_endpoint = PreferredEndpointInputField(
        required=False,
        allow_null=True,
        write_only=True,
    )
    template_code = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    enabled_personal_server_ids = serializers.ListField(
        child=serializers.UUIDField(format='hex_verbose'),
        required=False,
        write_only=True,
        allow_empty=True,
    )
    available_mcp_servers = serializers.SerializerMethodField(read_only=True)
    personal_mcp_server_ids = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PersistentAgent
        fields = [
            'id',
            'name',
            'charter',
            'short_description',
            'schedule',
            'schedule_snapshot',
            'is_active',
            'life_state',
            'whitelist_policy',
            'last_interaction_at',
            'created_at',
            'updated_at',
            'user_id',
            'organization_id',
            'browser_use_agent_id',
            'preferred_contact_endpoint_id',
            'preferred_contact_endpoint',
            'proactive_opt_in',
            'proactive_last_trigger_at',
            'template_code',
            'enabled_personal_server_ids',
            'available_mcp_servers',
            'personal_mcp_server_ids',
        ]
        read_only_fields = (
            'id',
            'short_description',
            'last_interaction_at',
            'created_at',
            'updated_at',
            'user_id',
            'organization_id',
            'browser_use_agent_id',
            'preferred_contact_endpoint_id',
            'proactive_last_trigger_at',
            'available_mcp_servers',
            'personal_mcp_server_ids',
        )
        ref_name = "PersistentAgentDetail"

    def validate_preferred_contact_endpoint(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        instance = getattr(self, 'instance', None)
        if value.owner_agent_id and (instance is None or value.owner_agent_id != instance.id):
            raise serializers.ValidationError("Contact endpoint belongs to a different agent.")
        return value

    def get_available_mcp_servers(self, obj: PersistentAgent) -> list[dict]:
        from .services import mcp_servers as server_service

        return server_service.agent_server_overview(obj)

    def get_personal_mcp_server_ids(self, obj: PersistentAgent) -> list[str]:
        from .services import mcp_servers as server_service

        return server_service.agent_enabled_personal_server_ids(obj)

    def create(self, validated_data):
        personal_servers = validated_data.pop('enabled_personal_server_ids', None)
        agent = super().create(validated_data)
        if personal_servers is not None:
            self._apply_personal_servers(agent, personal_servers)
        return agent

    def update(self, instance, validated_data):
        personal_servers = validated_data.pop('enabled_personal_server_ids', None)
        agent = super().update(instance, validated_data)
        if personal_servers is not None:
            self._apply_personal_servers(agent, personal_servers)
        return agent

    def _apply_personal_servers(self, agent: PersistentAgent, server_ids):
        from .services import mcp_servers as server_service

        try:
            server_service.update_agent_personal_servers(agent, [str(s) for s in server_ids])
        except ValueError as exc:
            raise serializers.ValidationError({'enabled_personal_server_ids': [str(exc)]})

    def _resolve_preferred_endpoint_channel(self, agent, channel_key: str):
        try:
            channel = CommsChannel(channel_key)
        except ValueError:
            raise serializers.ValidationError({'preferred_contact_endpoint': ['Unsupported contact channel.']})

        endpoint = (
            PersistentAgentCommsEndpoint.objects.filter(owner_agent=agent, channel=channel.value)
            .order_by('-is_primary', 'address')
            .first()
        )
        if endpoint:
            return endpoint

        request = self.context.get('request')
        if request is None:
            raise serializers.ValidationError({'preferred_contact_endpoint': ['Request context unavailable.']})

        if agent.organization_id:
            raise serializers.ValidationError({'preferred_contact_endpoint': [f"Agent has no {channel.value} endpoint available."]})

        if channel == CommsChannel.EMAIL:
            email = (request.user.email or "").strip().lower()
            if not email:
                raise serializers.ValidationError({'preferred_contact_endpoint': ['User email required to select email contact endpoint.']})
            endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                channel=CommsChannel.EMAIL,
                address=email,
                defaults={'owner_agent': None},
            )
            return endpoint

        if channel == CommsChannel.SMS:
            from util.sms import get_user_primary_sms_number

            sms_number = get_user_primary_sms_number(request.user)
            if sms_number is None:
                raise serializers.ValidationError({'preferred_contact_endpoint': ['User has no verified primary SMS number.']})

            endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                channel=CommsChannel.SMS,
                address=sms_number.phone_number,
                defaults={'owner_agent': None},
            )
            return endpoint

        raise serializers.ValidationError({'preferred_contact_endpoint': ['Unsupported contact channel.']})

    def create(self, validated_data):
        from api.services.persistent_agents import (
            PersistentAgentProvisioningError,
            PersistentAgentProvisioningService,
        )

        request = self.context['request']
        organization = self.context.get('organization')
        template_code = validated_data.pop('template_code', None)
        preferred_input = validated_data.pop('preferred_contact_endpoint', None)
        preferred_endpoint = preferred_input if not isinstance(preferred_input, str) else None
        preferred_channel = preferred_input if isinstance(preferred_input, str) else None

        with transaction.atomic():
            provision_kwargs = {
                'user': request.user,
                'organization': organization,
                'name': validated_data.get('name'),
                'charter': validated_data.get('charter'),
                'schedule': validated_data.get('schedule'),
                'is_active': validated_data.get('is_active', True),
                'life_state': validated_data.get('life_state'),
                'whitelist_policy': validated_data.get('whitelist_policy'),
                'preferred_contact_endpoint': preferred_endpoint,
                'template_code': template_code or None,
            }

            try:
                result = PersistentAgentProvisioningService.provision(**provision_kwargs)
            except PersistentAgentProvisioningError as exc:
                detail = exc.args[0] if exc.args else str(exc)
                if isinstance(detail, dict):
                    raise serializers.ValidationError(detail) from exc
                if isinstance(detail, list):
                    raise serializers.ValidationError(detail) from exc
                raise serializers.ValidationError({'non_field_errors': [str(exc)]}) from exc

            agent = result.agent

            if agent and preferred_channel:
                resolved_endpoint = self._resolve_preferred_endpoint_channel(agent, preferred_channel)
                agent.preferred_contact_endpoint = resolved_endpoint
                agent.save(update_fields=['preferred_contact_endpoint'])

            # If incoming payload explicitly provided fields that differ from defaults,
            # ensure they are persisted after provisioning.
            post_create_updates = {}
            for field in ('charter', 'schedule', 'is_active', 'life_state', 'whitelist_policy'):
                if field in validated_data and getattr(agent, field) != validated_data[field]:
                    setattr(agent, field, validated_data[field])
                    post_create_updates[field] = validated_data[field]
            if post_create_updates:
                try:
                    agent.full_clean()
                except DjangoValidationError as exc:
                    raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages) from exc
                agent.save(update_fields=list(post_create_updates.keys()))

            from api.agent.tasks import process_agent_events_task

            agent_id = str(agent.id)
            transaction.on_commit(lambda: process_agent_events_task.delay(agent_id))

        return agent

    def update(self, instance, validated_data):
        preferred_input = validated_data.pop('preferred_contact_endpoint', serializers.empty)
        preferred_channel = None
        preferred_endpoint = preferred_input
        if preferred_input is not serializers.empty and isinstance(preferred_input, str):
            preferred_channel = preferred_input
            preferred_endpoint = serializers.empty

        validated_data.pop('template_code', None)

        dirty_fields = set()
        for field, value in validated_data.items():
            setattr(instance, field, value)
            dirty_fields.add(field)

        if preferred_channel:
            resolved = self._resolve_preferred_endpoint_channel(instance, preferred_channel)
            instance.preferred_contact_endpoint = resolved
            dirty_fields.add('preferred_contact_endpoint')
        elif preferred_endpoint is not serializers.empty:
            instance.preferred_contact_endpoint = preferred_endpoint
            dirty_fields.add('preferred_contact_endpoint')

        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages) from exc

        update_fields = list(dirty_fields)
        instance.save(update_fields=update_fields or None)
        return instance
