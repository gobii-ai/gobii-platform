# gobii_platform/api/serializers.py
from rest_framework import serializers
from .models import ApiKey, BrowserUseAgent, BrowserUseAgentTask
from jsonschema import Draft202012Validator, ValidationError as JSValidationError

# Serializer for Listing Agents (id, name, created_at)
class BrowserUseAgentListSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True, format='hex_verbose')
    class Meta:
        model = BrowserUseAgent
        fields = ['id', 'name', 'created_at']
        ref_name = "AgentList" # Optional: for explicit component naming

class BrowserUseAgentSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True, format='hex_verbose')
    user_email = serializers.ReadOnlyField(source='user.email')

    class Meta:
        model = BrowserUseAgent
        fields = ['id', 'user_email', 'name', 'created_at', 'updated_at']
        read_only_fields = ('id', 'user_email', 'created_at', 'updated_at') # 'name' is now writable
        ref_name = "AgentDetail" # Optional: for explicit component naming

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
    wait = serializers.IntegerField(min_value=0, max_value=1350, required=False, write_only=True)
    secrets = serializers.DictField(
        required=False,
        write_only=True,  # Never return secrets in responses
        help_text="Domain-specific secrets for the task. REQUIRED FORMAT: {'https://example.com': {'x_api_key': 'value', 'x_username': 'user'}}. Each domain can have multiple secrets. Secret keys will be available as placeholders in the prompt for the specified domains."
    )
    credits_cost = serializers.DecimalField(max_digits=12, decimal_places=3, min_value="0.001", required=False, allow_null=True)

    class Meta:
        model = BrowserUseAgentTask
        fields = ['id', 'agent', 'agent_id', 'prompt', 'output_schema', 'status', 'created_at', 'updated_at', 'error_message', 'wait', 'secrets', 'credits_cost']
        read_only_fields = ('id', 'agent_id', 'status', 'created_at', 'updated_at', 'error_message')
        # 'prompt' and 'output_schema' are writable by not being in read_only_fields
        ref_name = "TaskDetail" # Optional: for explicit component naming

    def validate_prompt(self, value):
        # Accept both strings and dictionaries
        if value is not None and not isinstance(value, (dict, str)):
            raise serializers.ValidationError("prompt must be a string or a valid JSON object.")
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

    class Meta:
        model = BrowserUseAgentTask
        fields = ['id', 'agent_id', 'prompt', 'output_schema', 'status', 'created_at', 'updated_at', 'credits_cost']
        read_only_fields = fields
        ref_name = "TaskList" # Optional: for explicit component naming
