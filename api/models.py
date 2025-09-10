import hashlib, secrets, uuid, os, string

import ulid
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import UniqueConstraint
from django.db.models.functions.datetime import TruncMonth
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.utils.text import get_valid_filename

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, pre_delete

from django.dispatch import receiver

from agents.services import AgentService
from config.plans import PLAN_CONFIG
from config.settings import INITIAL_TASK_CREDIT_EXPIRATION_DAYS
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNames, PlanNamesChoices
from constants.regex import E164_PHONE_REGEX
from observability import traced
from email.utils import parseaddr

from tasks.services import TaskCreditService

from util.subscription_helper import (
    get_active_subscription, )
from datetime import timedelta

import logging
from opentelemetry import trace

try:
    import stripe
    from djstripe.models import Subscription

    DJSTRIPE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    stripe = None  # type: ignore
    Subscription = None  # type: ignore
    DJSTRIPE_AVAILABLE = False

# Helper to generate lexicographically sortable ULIDs as 26-char strings.
# Placed before model declarations so it's available during class body evaluation.
logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')

def generate_ulid() -> str:
    """Return a 26-character, time-ordered ULID string."""
    return str(ulid.new())

def _hash(raw: str) -> str:
    """Return SHA256 hexdigest for given raw string."""
    return hashlib.sha256(raw.encode()).hexdigest()

def get_default_execution_environment() -> str:
    """Return the default execution environment from GOBII_RELEASE_ENV."""
    return os.getenv("GOBII_RELEASE_ENV", "local")

class CommsChannel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    SLACK = "slack", "Slack"
    DISCORD = "discord", "Discord"
    OTHER = "other", "Other"


class DeliveryStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent to provider"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"

class SmsProvider(models.TextChoices):
    TWILIO = "twilio", "Twilio"

class ApiKey(models.Model):
    MAX_API_KEYS_PER_USER = 50

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    name = models.CharField(max_length=64, default="default")
    prefix = models.CharField(max_length=8, editable=False)
    hashed_key = models.CharField(max_length=64, editable=False)
    raw_key = models.CharField(max_length=128, editable=False, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=['user', 'name'], name='unique_api_key_user_name')
        ]

    @staticmethod
    def generate() -> tuple[str, str]:
        raw = secrets.token_urlsafe(32)
        return raw, _hash(raw)

    @classmethod
    def create_for_user(cls, user, name="default"):
        raw, hashed = cls.generate()
        prefix = raw[:8]
        instance = cls.objects.create(
            user=user,
            name=name,
            prefix=prefix,
            hashed_key=hashed,
            raw_key=raw,
        )
        return raw, instance

    def clean(self):
        super().clean()
        if self._state.adding:
            user_id = getattr(self, 'user_id', None)
            if user_id:
                current_key_count = ApiKey.objects.filter(user_id=user_id).count()
                if current_key_count >= self.MAX_API_KEYS_PER_USER:
                    User = get_user_model()
                    try:
                        user_email = User.objects.get(id=user_id).email
                    except User.DoesNotExist:
                        user_email = "Unknown"
                    raise ValidationError(
                        f"You have reached the maximum limit of {self.MAX_API_KEYS_PER_USER} API keys."
                    )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def matches(self, raw: str) -> bool:
        return self.hashed_key == _hash(raw) and self.revoked_at is None

    def revoke(self):
        self.revoked_at = timezone.now()
        self.save(update_fields=['revoked_at'])
        return self

    @property
    def is_active(self):
        return self.revoked_at is None


class UserQuota(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quota"
    )
    agent_limit = models.PositiveIntegerField(default=5)

    def __str__(self):
        return f"Quota for {self.user.email}"

class TaskCredit(models.Model):
    """Discrete block of task credits granted to a user."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="task_credits",
        null=True,
        blank=True,
    )
    # New: organization-owned task credits (mutually exclusive with user)
    organization = models.ForeignKey(
        'Organization',
        on_delete=models.CASCADE,
        related_name='task_credits',
        null=True,
        blank=True,
        help_text="Exactly one of user or organization must be set."
    )
    # Support fractional credits by using DecimalField
    credits = models.DecimalField(max_digits=12, decimal_places=3)
    credits_used = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    granted_date = models.DateTimeField()
    expiration_date = models.DateTimeField()
    stripe_invoice_id = models.CharField(max_length=128, null=True, blank=True)
    plan = models.CharField(
        max_length=32,
        choices=PlanNamesChoices.choices,
        default=PlanNames.FREE,
        help_text="The plan under which these credits were granted"
    )
    additional_task = models.BooleanField(
        default=False,
        help_text="Whether this credit was granted as an additional task beyond the plan limits"
    )

    available_credits = models.GeneratedField(
        expression=models.F('credits') - models.F('credits_used'),
        output_field=models.DecimalField(max_digits=12, decimal_places=3),
        db_persist=True,  # Stored generated column
    )

    grant_month = models.GeneratedField(
        expression=TruncMonth("granted_date"),     # → date_trunc('month', …)
        output_field=models.DateField(),
        db_persist=True,
    )

    grant_type = models.CharField(
        max_length=32,
        choices=GrantTypeChoices.choices,
        default=GrantTypeChoices.PLAN,
        help_text="Type of grant for these credits (e.g., PLAN, COMPENSATION, PROMO)"
    )

    voided = models.BooleanField(
        default=False,
        help_text="Whether this credit block has been voided and should not be used"
    )

    class Meta:
        ordering = ["-granted_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "plan", "grant_month"],
                condition=models.Q(
                    plan=PlanNames.FREE,
                    grant_type=GrantTypeChoices.PLAN,
                    additional_task=False,
                    voided=False
                ),
                name="uniq_free_plan_block_per_month",
            ),
            # Mirror uniqueness for organization ownership
            models.UniqueConstraint(
                fields=["organization", "plan", "grant_month"],
                condition=models.Q(
                    plan=PlanNames.FREE,
                    grant_type=GrantTypeChoices.PLAN,
                    additional_task=False,
                    voided=False
                ),
                name="uniq_free_plan_block_per_month_org",
            ),
            # Enforce exactly one owner: user XOR organization
            models.CheckConstraint(
                check=(
                    (
                        models.Q(user__isnull=False, organization__isnull=True)
                    ) | (
                        models.Q(user__isnull=True, organization__isnull=False)
                    )
                ),
                name="taskcredit_owner_xor_user_org",
            ),
        ]

    @property
    def remaining(self):
        return (self.credits or 0) - (self.credits_used or 0)

class BrowserUseAgent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agents"
    )
    name = models.CharField(max_length=64)
    preferred_proxy = models.ForeignKey(
        'ProxyServer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="browser_agents",
        help_text="Preferred proxy server for this browser agent"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            UniqueConstraint(fields=['user', 'name'], name='unique_browser_use_agent_user_name')
        ]
        indexes = [
            models.Index(fields=['preferred_proxy']),
        ]

    def __str__(self):
        return f"BrowserUseAgent: {self.name} (User: {getattr(self.user, 'email', 'N/A')})"

    def clean(self):
        super().clean()
        if self._state.adding and getattr(self, 'user_id', None):
            agents_available = AgentService.get_agents_available(self.user)

            # Regardless of plan type, if no slots remain we raise a validation
            # error.  ``AgentService`` already applies the global safety cap.
            if agents_available <= 0:
                raise ValidationError(
                    "Agent limit reached for this user."
                )

    @classmethod
    def select_random_proxy(cls):
        """Select a random proxy, preferring ones with recent successful health checks and static IPs"""
        return cls._select_proxy_with_health_preference()
    
    @classmethod
    def _select_proxy_with_health_preference(cls):
        """Select proxy with preference for recent health check passes"""
        from datetime import timedelta
        from django.utils import timezone

        with traced("SELECT BrowserUseAgent Random Proxy") as span:
            # Consider health checks from the last 45 days as "recent"
            recent_cutoff = timezone.now() - timedelta(days=45)

            # First priority: Static IP proxies with recent successful health checks
            with traced("SELECT BrowserUseAgent Healthy Static Proxy"):
                healthy_static_proxy = ProxyServer.objects.filter(
                    is_active=True,
                    static_ip__isnull=False,
                    health_check_results__status='PASSED',
                    health_check_results__checked_at__gte=recent_cutoff
                ).distinct().order_by('?').first()

            if healthy_static_proxy:
                span.set_attribute('proxy_choice', str(healthy_static_proxy.id))
                span.set_attribute('proxy_choice.ip', healthy_static_proxy.static_ip)
                span.set_attribute('proxy_choice.host', healthy_static_proxy.host)
                span.set_attribute('proxy_choice.port', healthy_static_proxy.port)
                span.set_attribute('proxy_choice.proxy_type', healthy_static_proxy.proxy_type)
                span.set_attribute('proxy_choice.username', healthy_static_proxy.username)
                span.set_attribute('proxy_choice.priority', '1')
                return healthy_static_proxy

            # Second priority: Any proxy with recent successful health checks
            with traced("SELECT BrowserUseAgent Healthy Static Proxy 2nd Priority"):
                healthy_proxy = ProxyServer.objects.filter(
                    is_active=True,
                    health_check_results__status='PASSED',
                    health_check_results__checked_at__gte=recent_cutoff
                ).distinct().order_by('?').first()

            if healthy_proxy:
                span.set_attribute('proxy_choice', str(healthy_proxy.id))
                span.set_attribute('proxy_choice.ip', healthy_proxy.static_ip)
                span.set_attribute('proxy_choice.host', healthy_proxy.host)
                span.set_attribute('proxy_choice.port', healthy_proxy.port)
                span.set_attribute('proxy_choice.proxy_type', healthy_proxy.proxy_type)
                span.set_attribute('proxy_choice.username', healthy_proxy.username)
                span.set_attribute('proxy_choice.priority', '2')
                return healthy_proxy

            # Third priority: Static IP proxies (even without recent health checks)
            with traced("SELECT BrowserUseAgent Healthy Static Proxy - 3rd Priority"):
                static_ip_proxy = ProxyServer.objects.filter(
                    is_active=True,
                    static_ip__isnull=False
                ).exclude(static_ip='').order_by('?').first()

            if static_ip_proxy:
                span.set_attribute('proxy_choice', str(static_ip_proxy.id))
                span.set_attribute('proxy_choice.ip', static_ip_proxy.static_ip)
                span.set_attribute('proxy_choice.host', static_ip_proxy.host)
                span.set_attribute('proxy_choice.port', static_ip_proxy.port)
                span.set_attribute('proxy_choice.proxy_type', static_ip_proxy.proxy_type)
                span.set_attribute('proxy_choice.username', static_ip_proxy.username)
                span.set_attribute('proxy_choice.priority', '3')
                return static_ip_proxy

            # Final fallback: Any active proxy
            with traced("SELECT BrowserUseAgent Any Active Proxy"):

                # This will return any active proxy, regardless of health checks
                # or static IP status
                proxy = ProxyServer.objects.filter(
                    is_active=True
                ).order_by('?').first()

                if proxy:
                    span.set_attribute('proxy_choice', str(proxy.id))
                    span.set_attribute('proxy_choice.ip', proxy.static_ip)
                    span.set_attribute('proxy_choice.host', proxy.host)
                    span.set_attribute('proxy_choice.port', proxy.port)
                    span.set_attribute('proxy_choice.proxy_type', proxy.proxy_type)
                    span.set_attribute('proxy_choice.username', proxy.username)
                    span.set_attribute('proxy_choice.priority', '4')

                return proxy

    def save(self, *args, **kwargs):
        # Auto-assign proxy on creation if none is set
        if self._state.adding and not self.preferred_proxy_id:
            self.preferred_proxy = self.select_random_proxy()
        
        self.full_clean()
        super().save(*args, **kwargs)


class BrowserUseAgentTaskQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(is_deleted=False)


class BrowserUseAgentTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        BrowserUseAgent,
        on_delete=models.CASCADE,
        related_name="tasks",
        null=True,
        blank=True,
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_tasks", null=True, blank=True)
    # Credit used for this task
    task_credit = models.ForeignKey(
        "TaskCredit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    # prompt can be a simple string or a JSON structure. Using JSONField is more flexible.
    prompt = models.TextField(blank=True, null=True)
    # Optional JSON schema to define structured output from the agent
    output_schema = models.JSONField(
        null=True,
        blank=True,
        help_text="Optional JSON schema to define structured output from the agent"
    )

    # New fields for secrets support
    encrypted_secrets = models.BinaryField(null=True, blank=True)
    secret_keys = models.JSONField(
        null=True,
        blank=True,
        help_text="Dictionary mapping domain patterns to secret keys (for audit purposes). Format: {'https://example.com': ['key1', 'key2']}"
    )

    class StatusChoices(models.TextChoices):
        PENDING = 'pending', 'Pending'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled' # Added CANCELLED

    status = models.CharField(
        max_length=50,
        choices=StatusChoices.choices,
        default=StatusChoices.PENDING
    )
    error_message = models.TextField(null=True, blank=True)
    # Token usage tracking fields
    prompt_tokens = models.IntegerField(
        null=True,
        blank=True,
        help_text="Number of tokens used in the prompt for this step's LLM call",
    )
    completion_tokens = models.IntegerField(
        null=True,
        blank=True,
        help_text="Number of tokens generated in the completion for this step's LLM call",
    )
    total_tokens = models.IntegerField(
        null=True,
        blank=True,
        help_text="Total tokens used (prompt + completion) for this step's LLM call",
    )
    # Credits charged for this task (for audit). If not provided, defaults to configured per‑task cost.
    credits_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Credits charged for this task; defaults to configured per‑task cost.",
    )
    cached_tokens = models.IntegerField(
        null=True,
        blank=True,
        help_text="Number of cached tokens used (if provider supports caching)",
    )
    llm_model = models.CharField(
        max_length=256,
        null=True,
        blank=True,
        help_text="LLM model used for this step (e.g., 'claude-3-opus-20240229')",
    )
    llm_provider = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="LLM provider used for this step (e.g., 'anthropic', 'openai')",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Fields for soft delete
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = BrowserUseAgentTaskQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at'], name='task_status_created_idx'),
            models.Index(fields=['created_at'], name='task_created_idx'),
        ]

    def __str__(self):
        agent_part = f"Agent {self.agent.name}" if self.agent else "No Agent"
        return f"BrowserUseAgentTask {self.id} ({agent_part}) (User: {getattr(self.user, 'email', 'N/A')})"

    def clean(self):
        super().clean()
        if self._state.adding:
            with traced("CHECK Clean BrowserUseAgentTask User Credit") as span:
                # For health check tasks (user=None), skip user validation
                if self.user_id is None:
                    return
                else:
                    span.set_attribute("user.id", str(self.user_id))

                # For regular user tasks, enforce validation
                if not self.user.is_active:
                    raise ValidationError({'subscription': 'Inactive user. Cannot create tasks.'})

                # Determine owner: organization if this task is for an org-owned PersistentAgent; otherwise the user
                owner_org = None
                try:
                    if self.agent and hasattr(self.agent, 'persistent_agent') and self.agent.persistent_agent:
                        owner_org = self.agent.persistent_agent.organization
                except Exception:
                    owner_org = None

                # We need a helper layer on top of this stuff to unify the logic; known duplication here
                if owner_org:
                    task_credits = TaskCredit.objects.filter(
                        organization=owner_org, expiration_date__gte=timezone.now(), voided=False
                    )
                else:
                    task_credits = TaskCredit.objects.filter(
                        user=self.user, expiration_date__gte=timezone.now(), voided=False
                    )
                available_tasks = sum(tc.remaining for tc in task_credits)

                subscription = get_active_subscription(self.user) if not owner_org else None

                # If no active subscription and no remaining credits, block task creation
                if available_tasks <= 0 and subscription is None:
                    raise ValidationError(
                        {"quota": f"Task quota exceeded. Used: {available_tasks}"}
                    )

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.full_clean()
        # Skip quota handling for health check tasks (user=None)
        if self._state.adding and self.user_id:
            with transaction.atomic():
                # Determine owner (organization or user) and consume accordingly
                owner = self.user
                if self.agent:
                    try:
                        pa = self.agent.persistent_agent
                    except Exception:
                        pa = None
                    if pa and getattr(pa, 'organization', None):
                        owner = pa.organization

                # Use consolidated credit checking and consumption logic (owner-aware)
                # Determine amount to consume; persist it on the task for auditability
                amount = self.credits_cost if self.credits_cost is not None else settings.CREDITS_PER_TASK
                result = TaskCreditService.check_and_consume_credit_for_owner(owner, amount=amount)
                
                if not result['success']:
                    raise ValidationError({"quota": result['error_message']})
                
                # Associate the consumed credit with this task
                self.task_credit = result['credit']
                # Persist the actual credits charged for this task
                if self.credits_cost is None:
                    self.credits_cost = amount

                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)


class BrowserUseAgentTaskStep(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(BrowserUseAgentTask, on_delete=models.CASCADE, related_name="steps")
    step_number = models.PositiveIntegerField()
    description = models.TextField()
    is_result = models.BooleanField(default=False)
    result_value = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['task', 'step_number']
        constraints = [
            UniqueConstraint(fields=['task', 'step_number'], name='unique_browser_use_agent_task_step_task_step_number')
        ]

    def __str__(self):
        return f"Step {self.step_number} for Task {self.task.id}"

    def clean(self):
        super().clean()
        if self.is_result and not self.result_value:
            raise ValidationError({'result_value': 'Result value cannot be empty if this step is marked as the result.'})
        if not self.is_result and self.result_value:
            raise ValidationError({'result_value': 'Result value should only be set if this step is marked as the result.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


@receiver(post_save, sender=get_user_model())
def initialize_new_user_resources(sender, instance, created, **kwargs):
    if created:
        with traced("INITIALIZE User"):
            UserQuota.objects.create(user=instance)

            # Grant initial task credits based on the user's plan
            now = timezone.now()
            expires = now + timedelta(days=INITIAL_TASK_CREDIT_EXPIRATION_DAYS)

            # Note: since this is a new, they are automatically on the free plan. The might immediately upgrade, but
            # we still want to give them some initial credits here in case they do not
            credit_amount = PLAN_CONFIG[PlanNames.FREE]["monthly_task_credits"]

            if credit_amount > 0:
                # Only create TaskCredit if the user has a positive credit limit
                # This avoids creating TaskCredit with 0 credits
                with traced("CREATE User TaskCredit", user_id=instance.id):
                    TaskCredit.objects.create(
                        user=instance,
                        credits=credit_amount,
                        granted_date=now,
                        expiration_date=expires,
                        plan=PlanNamesChoices.FREE,
                        additional_task=False,
                        grant_type=GrantTypeChoices.PLAN,
                        voided=False,
                    )

            # Automatically create a default API key for new users
            with traced("CREATE User API Key"):
                ApiKey.create_for_user(user=instance, name="default")

            # Create an initial billing record for the user
            with traced("CREATE User Billing Record", user_id=instance.id):
                try:
                    UserBilling.objects.create(
                        user=instance,
                        billing_cycle_anchor=instance.date_joined.day,
                    )
                except Exception as e:
                    logger.error(f"Error creating billing record for user {instance.id}: {e}")
                    pass


class PaidPlanIntent(models.Model):
    """Track users who have shown interest in paid plans"""

    class PlanChoices(models.TextChoices):
        STARTUP = 'startup', 'Startup'
        ENTERPRISE = 'enterprise', 'Enterprise'
        # Add more as needed

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="plan_intents"
    )
    plan_name = models.CharField(
        max_length=32,
        choices=PlanChoices.choices
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    extra = models.JSONField(
        null=True,
        blank=True,
        help_text="Optional metadata (utm params, referrer, etc)"
    )

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=['user', 'plan_name'],
                name='unique_user_plan_intent'
            )
        ]
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.user.email} - {self.get_plan_name_display()} (requested {self.requested_at.date()})"


class ProxyServer(models.Model):
    """Generic proxy server configuration"""
    
    class ProxyType(models.TextChoices):
        HTTP = "HTTP", "HTTP"
        HTTPS = "HTTPS", "HTTPS" 
        SOCKS4 = "SOCKS4", "SOCKS4"
        SOCKS5 = "SOCKS5", "SOCKS5"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, help_text="Human-readable name for this proxy")
    proxy_type = models.CharField(
        max_length=8,
        choices=ProxyType.choices,
        default=ProxyType.HTTP,
        help_text="Type of proxy protocol"
    )
    host = models.CharField(max_length=256, help_text="Proxy server hostname or IP")
    port = models.PositiveIntegerField(help_text="Proxy server port")
    
    # Authentication (optional)
    username = models.CharField(max_length=128, blank=True, help_text="Username for proxy authentication")
    password = models.CharField(max_length=128, blank=True, help_text="Password for proxy authentication")
    
    # Static IP tracking (optional)
    static_ip = models.GenericIPAddressField(
        null=True, 
        blank=True, 
        help_text="Static IP address assigned to this proxy (if known)"
    )
    
    # Decodo IP association (optional)
    decodo_ip = models.OneToOneField(
        'DecodoIP',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='proxy_server',
        help_text="Associated Decodo IP record (if this proxy is from Decodo)"
    )
    
    # Status and metadata
    is_active = models.BooleanField(default=True, help_text="Whether this proxy is currently active")
    notes = models.TextField(blank=True, help_text="Additional notes about this proxy server")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            UniqueConstraint(fields=['host', 'port'], name='unique_proxy_server_host_port')
        ]
        indexes = [
            models.Index(fields=['host']),
            models.Index(fields=['port']),
            models.Index(fields=['proxy_type']),
            models.Index(fields=['is_active']),
            models.Index(fields=['static_ip']),
            # Composite index for efficient proxy selection queries
            models.Index(fields=['is_active', 'static_ip'], name='proxy_active_static_ip_idx'),
        ]

    def __str__(self):
        auth_part = f"{self.username}@" if self.username else ""
        static_ip_part = f" (IP: {self.static_ip})" if self.static_ip else ""
        return f"{self.name}: {auth_part}{self.host}:{self.port}{static_ip_part}"

    @property
    def proxy_url(self) -> str:
        """Generate proxy URL for use with requests library"""
        scheme = self.proxy_type.lower()
        if self.username and self.password:
            return f"{scheme}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def requires_auth(self) -> bool:
        """Check if this proxy requires authentication"""
        return bool(self.username and self.password)


class DecodoCredential(models.Model):
    """Decodo dedicated residential IP credentials"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=128)
    password = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            UniqueConstraint(fields=['username'], name='unique_decodo_credential_username')
        ]

    def __str__(self):
        return f"DecodoCredential: {self.username}"


class DecodoIPBlock(models.Model):
    """Decodo dedicated residential IP block"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    credential = models.ForeignKey(
        DecodoCredential,
        on_delete=models.CASCADE,
        related_name="ip_blocks"
    )
    block_size = models.PositiveIntegerField(help_text="Number of IPs in this block (e.g. 50)")
    endpoint = models.CharField(max_length=256, help_text="Proxy endpoint (e.g. 'isp.decodo.com')")
    start_port = models.PositiveIntegerField(help_text="Starting port number (e.g. 10001)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['endpoint']),
            models.Index(fields=['start_port']),
        ]

    def __str__(self):
        return f"DecodoIPBlock: {self.endpoint}:{self.start_port} (size: {self.block_size})"


class DecodoIP(models.Model):
    """Individual Decodo IP address with location and ISP information"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ip_block = models.ForeignKey(
        DecodoIPBlock,
        on_delete=models.CASCADE,
        related_name="ip_addresses"
    )

    # Proxy information
    ip_address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(help_text="Port number used to discover this IP")

    # ISP information
    isp_name = models.CharField(max_length=256, blank=True)
    isp_asn = models.PositiveIntegerField(null=True, blank=True)
    isp_domain = models.CharField(max_length=256, blank=True)
    isp_organization = models.CharField(max_length=256, blank=True)

    # City information
    city_name = models.CharField(max_length=256, blank=True)
    city_code = models.CharField(max_length=32, blank=True)
    city_state = models.CharField(max_length=256, blank=True)
    city_timezone = models.CharField(max_length=64, blank=True)
    city_zip_code = models.CharField(max_length=32, blank=True)
    city_latitude = models.FloatField(null=True, blank=True)
    city_longitude = models.FloatField(null=True, blank=True)

    # Country information
    country_code = models.CharField(max_length=8, blank=True)
    country_name = models.CharField(max_length=256, blank=True)
    country_continent = models.CharField(max_length=256, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            UniqueConstraint(fields=['ip_address'], name='unique_decodo_ip_address'),
            UniqueConstraint(fields=['ip_block', 'port'], name='unique_decodo_ip_block_port')
        ]
        indexes = [
            models.Index(fields=['ip_address']),
            models.Index(fields=['port']),
            models.Index(fields=['country_code']),
            models.Index(fields=['country_name']),
            models.Index(fields=['isp_name']),
            models.Index(fields=['isp_asn']),
            models.Index(fields=['city_name']),
            models.Index(fields=['city_state']),
            models.Index(fields=['city_latitude', 'city_longitude']),
        ]

    def __str__(self):
        location_parts = [self.city_name, self.city_state, self.country_name]
        location = ", ".join([part for part in location_parts if part])
        if location:
            return f"DecodoIP: {self.ip_address} ({location})"
        return f"DecodoIP: {self.ip_address}"

# api/models.py
class UserBilling(models.Model):
    """
    Billing information associated with a user.
    Each user has a one-to-one relationship with UserBilling.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing"
    )
    subscription = models.CharField(
        max_length=32,
        choices=PlanNamesChoices.choices,
        default=PlanNames.FREE,
        help_text="The user's subscription plan"
    )
    max_extra_tasks = models.IntegerField(
        default=0,
        help_text="Maximum number of additional tasks allowed beyond plan limits. 0 means no extra tasks, -1 means unlimited.",
    )

    billing_cycle_anchor = models.IntegerField(
        default=1,
        help_text="Day of the month when billing cycle starts (1-31). 1 means start on the 1st of each month.",
        validators=[
            MinValueValidator(1),
            MaxValueValidator(31),
        ]
    )
    downgraded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when user was downgraded to free (for soft-expiration grace)."
    )

    def __str__(self):
        return f"Billing for {self.user.email}"

    class Meta:
        verbose_name = "User Billing"
        verbose_name_plural = "User Billing"


class UserPhoneNumber(models.Model):
    """Phone numbers associated with a user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="phone_numbers",
    )
    phone_number = models.CharField(
        max_length=32,
        unique=True,
        validators=[RegexValidator(
            regex=E164_PHONE_REGEX,
            message="Phone number must be in E.164 format (e.g., +1234567890)",
        )],
    )
    is_primary = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    last_verification_attempt = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_sid = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_primary=True),
                name="uniq_primary_phone_per_user",
            ),
            models.CheckConstraint(
                check=models.Q(phone_number__regex=E164_PHONE_REGEX),
                name="chk_e164_user_phone",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.phone_number}"

class ProxyHealthCheckSpec(models.Model):
    """Specification for proxy health check tests"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, help_text="Human-readable name for this health check")
    prompt = models.TextField(help_text="Prompt that describes what the health check should do")
    is_active = models.BooleanField(default=True, help_text="Whether this health check spec is currently active")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return f"ProxyHealthCheckSpec: {self.name}"


class ProxyHealthCheckResult(models.Model):
    """Result of running a health check on a specific proxy"""
    
    class Status(models.TextChoices):
        PASSED = "PASSED", "Passed"
        FAILED = "FAILED", "Failed"
        ERROR = "ERROR", "Error"
        TIMEOUT = "TIMEOUT", "Timeout"
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proxy_server = models.ForeignKey(
        'ProxyServer',
        on_delete=models.CASCADE,
        related_name='health_check_results',
        help_text="The proxy server that was tested"
    )
    health_check_spec = models.ForeignKey(
        'ProxyHealthCheckSpec',
        on_delete=models.CASCADE,
        related_name='results',
        help_text="The health check specification that was used"
    )
    
    # Check execution details
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        help_text="Result of the health check"
    )
    checked_at = models.DateTimeField(default=timezone.now, help_text="When the check was performed")
    response_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Response time in milliseconds (if applicable)"
    )
    
    # Additional details
    error_message = models.TextField(
        blank=True,
        help_text="Error details if the check failed"
    )
    task_result = models.JSONField(
        null=True,
        blank=True,
        help_text="Full task result data from the browser use agent"
    )
    notes = models.TextField(blank=True, help_text="Additional notes about this check")
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-checked_at']
        indexes = [
            models.Index(fields=['proxy_server', '-checked_at']),
            models.Index(fields=['health_check_spec', '-checked_at']),
            models.Index(fields=['status']),
            models.Index(fields=["-checked_at"]),
            # Composite index for recent results by proxy and status
            models.Index(fields=['proxy_server', 'status', '-checked_at'], name='proxy_status_recent_idx'),
        ]
        constraints = [
            # Ensure we don't have duplicate checks for the same proxy/spec at the exact same time
            UniqueConstraint(
                fields=['proxy_server', 'health_check_spec', 'checked_at'],
                name='unique_proxy_spec_timestamp'
            )
        ]

    def __str__(self):
        return f"HealthCheck {self.status}: {self.proxy_server.host}:{self.proxy_server.port} @ {self.checked_at}"
    
    @property
    def passed(self) -> bool:
        """Convenience property to check if the health check passed"""
        return self.status == self.Status.PASSED


# Persistent Agents Models

class PersistentAgent(models.Model):
    """
    A persistent agent that runs automatically on a schedule.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="persistent_agents",
    )
    organization = models.ForeignKey(
        'Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='persistent_agents',
        help_text="Owning organization, if any. If null, owned by the creating user."
    )
    name = models.CharField(max_length=255)
    charter = models.TextField()
    schedule = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="Cron-like schedule expression or interval (e.g., '@daily', '@every 30m')."
    )
    browser_use_agent = models.OneToOneField(
        BrowserUseAgent,
        on_delete=models.CASCADE,
        related_name="persistent_agent"
    )
    is_active = models.BooleanField(default=True, help_text="Whether this agent is currently active")
    # Soft-expiration state and interaction tracking
    class LifeState(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"

    life_state = models.CharField(
        max_length=16,
        choices=LifeState.choices,
        default=LifeState.ACTIVE,
        help_text="Lifecycle state for soft-expiration. 'paused' is represented by is_active=False."
    )
    last_interaction_at = models.DateTimeField(
        null=True,
        blank=True,
        default=timezone.now,
        help_text="Timestamp of the last user interaction (reply, edit, etc.)."
    )
    schedule_snapshot = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="Snapshot of cron schedule for restoration."
    )
    last_expired_at = models.DateTimeField(null=True, blank=True)
    sleep_email_sent_at = models.DateTimeField(null=True, blank=True)

    class WhitelistPolicy(models.TextChoices):
        DEFAULT = "default", "Default (Owner or Org Members)"
        MANUAL = "manual", "Allowed Contacts List"

    whitelist_policy = models.CharField(
        max_length=16,
        choices=WhitelistPolicy.choices,
        default=WhitelistPolicy.MANUAL,  # Changed to MANUAL - all agents now use manual mode
        help_text=(
            "Controls who can message this agent and who the agent may contact. "
            "Manual: only addresses/numbers listed on the agent's allowlist (includes owner/org members by default)."
        ),
    )
    execution_environment = models.CharField(
        max_length=64,
        default=get_default_execution_environment,
        help_text="The execution environment this agent was created in (e.g., 'local', 'staging', 'prod')"
    )
    # Link to the endpoint we should use when contacting the *user* by default.
    # Typically this will be an email or SMS endpoint that is *not* owned by the agent
    # itself (owner_agent = None).
    preferred_contact_endpoint = models.ForeignKey(
        "PersistentAgentCommsEndpoint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preferred_by_agents",
        help_text="Communication endpoint (email/SMS/etc.) the agent should use by default to reach its owner user."
    )
    enabled_mcp_tools = models.JSONField(
        default=list,
        blank=True,
        help_text='List of enabled MCP tool names for this agent (e.g., ["mcp_brightdata_search_engine"])'
    )
    mcp_tool_usage = models.JSONField(
        default=dict,
        blank=True,
        help_text='Dictionary mapping MCP tool names to last usage timestamps for LRU tracking'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['schedule'], name='pa_schedule_idx'),
            models.Index(fields=['life_state', 'is_active'], name='pa_life_active_idx'),
            models.Index(fields=['last_interaction_at'], name='pa_last_interact_idx'),
        ]
        constraints = [
            # Unique per user when no organization is set
            UniqueConstraint(
                fields=['user', 'name'],
                name='unique_persistent_agent_user_name',
                condition=models.Q(organization__isnull=True),
            ),
            # Unique per organization when organization is set
            UniqueConstraint(
                fields=['organization', 'name'],
                name='unique_persistent_agent_org_name',
                condition=models.Q(organization__isnull=False),
            ),
        ]

    def clean(self):
        """Custom validation for the agent."""
        super().clean()
        if self.schedule:
            try:
                # Use the same parser that's used for task scheduling to ensure consistency.
                from api.agent.core.schedule_parser import ScheduleParser
                ScheduleParser.parse(self.schedule)
            except ValueError as e:
                raise ValidationError({'schedule': str(e)})

    def __str__(self):
        schedule_display = self.schedule if self.schedule else "No schedule"
        return f"PersistentAgent: {self.name} (Schedule: {schedule_display})"

    @tracer.start_as_current_span("WHITELIST PersistentAgent Inbound Sender Check")
    def is_sender_whitelisted(self, channel: CommsChannel | str, address: str) -> bool:
        """Check if an inbound address/number is allowed to contact this agent."""
        channel_val = channel.value if isinstance(channel, CommsChannel) else str(channel)
        addr = (address or "").strip()
        addr_lower = addr.lower()

        logger.info("Whitelist check for channel: %s, address: %s, policy=%s", channel_val, addr_lower, self.whitelist_policy)

        if channel_val not in (CommsChannel.EMAIL, CommsChannel.SMS):
            logger.info("Whitelist check - Unsupported channel '%s'; defaulting to False", channel_val)
            return False

        if self.whitelist_policy == self.WhitelistPolicy.MANUAL:
            return self._is_in_manual_allowlist(channel_val, addr, direction="inbound")

        return self._is_allowed_default(channel_val, addr)

    @tracer.start_as_current_span("WHITELIST PersistentAgent Outbound Recipient Check")
    def is_recipient_whitelisted(self, channel: CommsChannel | str, address: str) -> bool:
        """Check if an outbound address/number is allowed for this agent."""
        channel_val = channel.value if isinstance(channel, CommsChannel) else str(channel)
        addr = (address or "").strip()

        if channel_val not in (CommsChannel.EMAIL, CommsChannel.SMS):
            return False
        
        # Block SMS for multi-player agents (org-owned only)
        # until group SMS functionality is implemented
        if channel_val == CommsChannel.SMS:
            if self.organization_id is not None:
                # Org-owned agents can only use email (group SMS not yet supported)
                return False

        if self.whitelist_policy == self.WhitelistPolicy.MANUAL:
            return self._is_in_manual_allowlist(channel_val, addr, direction="outbound")

        return self._is_allowed_default(channel_val, addr)

    def _legacy_owner_only(self, channel_val: str, address: str) -> bool:
        """Original behavior: only owner's email or verified phone allowed."""
        addr_raw = (address or "").strip()
        addr_lower = addr_raw.lower()
        if channel_val == CommsChannel.EMAIL:
            owner_email = (self.user.email or "").lower()
            email_only = (parseaddr(addr_raw)[1] or addr_lower).lower()
            return email_only == owner_email
        if channel_val == CommsChannel.SMS:
            from .models import UserPhoneNumber
            return UserPhoneNumber.objects.filter(
                user=self.user,
                phone_number__iexact=(address or "").strip(),
                is_verified=True,
            ).exists()
        return False

    def _is_in_manual_allowlist(self, channel_val: str, address: str, direction: str = "both") -> bool:
        """Return True if address is present in the agent-level manual allowlist for the given channel.
        
        Args:
            channel_val: The communication channel (email, sms, etc.)
            address: The address to check
            direction: "inbound" (can send to agent), "outbound" (agent can send to), or "both"
        
        Owner is always implicitly allowed even with manual allowlist policy.
        For org-owned agents, org members are also implicitly allowed.
        """
        addr = (address or "").strip()
        if channel_val == CommsChannel.EMAIL:
            # Normalize display-name formats like "Name <email@example.com>"
            addr = (parseaddr(addr)[1] or addr).lower()
            
            # Owner is always allowed
            owner_email = (self.user.email or "").lower()
            if addr == owner_email:
                return True
            
            # For org-owned agents, org members are implicitly allowed
            if self.organization_id:
                from .models import OrganizationMembership
                if OrganizationMembership.objects.filter(
                    org=self.organization,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                    user__email__iexact=addr,
                ).exists():
                    return True
                
        elif channel_val == CommsChannel.SMS:
            # Owner's verified phone is always allowed
            from .models import UserPhoneNumber
            if UserPhoneNumber.objects.filter(
                user=self.user,
                phone_number__iexact=addr,
                is_verified=True,
            ).exists():
                return True
            
            # For org-owned agents, any verified phone of org members is allowed
            if self.organization_id:
                from .models import OrganizationMembership
                if UserPhoneNumber.objects.filter(
                    user__organizationmembership__org=self.organization,
                    user__organizationmembership__status=OrganizationMembership.OrgStatus.ACTIVE,
                    phone_number__iexact=addr,
                    is_verified=True,
                ).exists():
                    return True
        
        # Check manual allowlist entries with direction
        try:
            query = CommsAllowlistEntry.objects.filter(
                agent=self,
                channel=channel_val,
                address__iexact=addr,
                is_active=True,
            )
            
            # Apply direction-specific filtering
            if direction == "inbound":
                query = query.filter(allow_inbound=True)
            elif direction == "outbound":
                query = query.filter(allow_outbound=True)
            elif direction == "both":
                # For "both", we check if either inbound or outbound is allowed
                # This is mainly for backward compatibility
                query = query.filter(
                    models.Q(allow_inbound=True) | models.Q(allow_outbound=True)
                )
            
            return query.exists()
        except Exception as e:
            logger.error(
                "Error checking manual allowlist for agent %s: %s", self.id, e, exc_info=True
            )
            return False

    def _is_allowed_default(self, channel_val: str, address: str) -> bool:
        """Default allow rules: owner-only for user-owned agents; org members for org-owned agents."""
        addr_raw = (address or "").strip()
        addr_lower = addr_raw.lower()
        # Email rules
        if channel_val == CommsChannel.EMAIL:
            # Normalize display-name formats like "Name <email@example.com>"
            email_only = (parseaddr(addr_raw)[1] or addr_lower).lower()
            if self.organization_id:
                # Org members by email
                from .models import OrganizationMembership
                return OrganizationMembership.objects.filter(
                    org=self.organization,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                    user__email__iexact=email_only,
                ).exists()
            # User-owned: owner email
            owner_email = (self.user.email or "").lower()
            whitelisted = email_only == owner_email
            logger.info("Whitelist default EMAIL check: %s === %s -> %s", email_only, owner_email, whitelisted)
            return whitelisted

        # SMS rules
        if channel_val == CommsChannel.SMS:
            from .models import UserPhoneNumber
            if self.organization_id:
                from .models import OrganizationMembership
                # Any verified number belonging to an active org member
                return UserPhoneNumber.objects.filter(
                    user__organizationmembership__org=self.organization,
                    user__organizationmembership__status=OrganizationMembership.OrgStatus.ACTIVE,
                    phone_number__iexact=address.strip(),
                    is_verified=True,
                ).exists()
            # User-owned: owner's verified number
            return UserPhoneNumber.objects.filter(
                user=self.user,
                phone_number__iexact=address.strip(),
                is_verified=True,
            ).exists()

        return False

    def _remove_celery_beat_task(self):
        """Removes the associated Celery Beat schedule task."""
        from celery import current_app as celery_app
        from redbeat import RedBeatSchedulerEntry

        task_name = f"persistent-agent-schedule:{self.id}"
        app = celery_app
        try:
            # Use the app instance to avoid potential context issues
            with app.connection():
                entry = RedBeatSchedulerEntry.from_key(f"redbeat:{task_name}", app=app)
                entry.delete()
            logger.info("Removed Celery Beat task for agent %s", self.id)
        except KeyError:
            # Task doesn't exist, which is fine.
            pass
        except Exception as e:
            # Catch other potential errors during deletion
            logger.error(
                "Error removing Celery Beat task for agent %s: %s", self.id, e
            )

    def _sync_celery_beat_task(self):
        """
        Creates, updates, or removes the Celery Beat task based on the agent's
        current state (schedule and is_active). This operation is atomic.
        """
        from celery import current_app as celery_app
        from redbeat import RedBeatSchedulerEntry
        from api.agent.core.schedule_parser import ScheduleParser

        task_name = f"persistent-agent-schedule:{self.id}"
        app = celery_app

        # Check if the agent's execution environment matches the current environment
        current_env = os.getenv("GOBII_RELEASE_ENV", "local")
        if self.execution_environment != current_env:
            logger.info(
                "Skipping Celery Beat task registration for agent %s: "
                "execution environment '%s' does not match current environment '%s'",
                self.id, self.execution_environment, current_env
            )
            return

        # If the agent is inactive or has no schedule, ensure the task is removed.
        if not self.is_active or not self.schedule:
            self._remove_celery_beat_task()
            return

        # Otherwise, create or update the task. RedBeat's save() performs an atomic upsert.
        try:
            schedule_obj = ScheduleParser.parse(self.schedule)
            if schedule_obj:
                entry = RedBeatSchedulerEntry(
                    name=task_name,
                    task="api.agent.tasks.process_agent_cron_trigger",
                    schedule=schedule_obj,
                    args=[str(self.id), self.schedule],  # Pass both agent ID and cron expression
                    app=app,
                )
                entry.save()
                logger.info(
                    "Synced Celery Beat task for agent %s with schedule '%s'",
                    self.id, self.schedule
                )
            else:
                # If parsing results in a null schedule (e.g. empty string), remove the task.
                self._remove_celery_beat_task()
        except ValueError as e:
            logger.error(
                "Failed to parse schedule '%s' for agent %s: %s. Removing existing task.",
                self.schedule, self.id, e
            )
            # If the new schedule is invalid, remove any old, lingering task.
            self._remove_celery_beat_task()
        except Exception as e:
            logger.error(
                "Error syncing Celery Beat task for agent %s: %s", self.id, e
            )

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        # For updates, we need to check if schedule-related fields have changed.
        sync_needed = False
        if not is_new:
            try:
                # Fetch the current state from the database before it's saved.
                old_instance = PersistentAgent.objects.get(pk=self.pk)
                if (old_instance.schedule != self.schedule or
                    old_instance.is_active != self.is_active):
                    sync_needed = True
            except PersistentAgent.DoesNotExist:
                # If it doesn't exist in the DB yet, treat it as a new instance.
                is_new = True

        # Proceed with the actual save operation. This is part of the transaction.
        super().save(*args, **kwargs)

        # If it's a new instance or a relevant field changed, schedule the
        # Redis side-effect to run only after a successful DB commit.
        if is_new or sync_needed:
            transaction.on_commit(self._sync_celery_beat_task)

    def delete(self, *args, **kwargs):
        # Schedule the removal of the Celery Beat task to happen only after
        # the database transaction that deletes this instance successfully commits.
        transaction.on_commit(self._remove_celery_beat_task)
        return super().delete(*args, **kwargs)


class PersistentAgentSecret(models.Model):
    """
    A secret (encrypted key-value pair) for a persistent agent, scoped to a domain pattern.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="secrets"
    )
    domain_pattern = models.CharField(
        max_length=256,
        help_text="Domain pattern where this secret can be used (e.g., 'https://example.com', '*.google.com')"
    )
    name = models.CharField(
        max_length=128,
        help_text="Human-readable name for this secret (e.g., 'X Password', 'API Key')"
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description of what this secret is used for"
    )
    key = models.CharField(
        max_length=64,
        blank=True,
        help_text="Secret key name (auto-generated from name, alphanumeric with underscores only)"
    )
    encrypted_value = models.BinaryField(
        help_text="AES-256-GCM encrypted secret value"
    )
    requested = models.BooleanField(
        default=False,
        help_text="Whether this secret has been requested but does not have a value yet"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['agent', 'domain_pattern', 'name'],
                name='unique_agent_domain_secret_name'
            ),
            models.UniqueConstraint(
                fields=['agent', 'domain_pattern', 'key'],
                name='unique_agent_domain_secret_key'
            )
        ]
        indexes = [
            models.Index(fields=['agent', 'domain_pattern'], name='pa_secret_agent_domain_idx'),
            models.Index(fields=['agent'], name='pa_secret_agent_idx'),
        ]
        ordering = ['domain_pattern', 'name']

    def generate_key_from_name(self):
        """Generate a unique key from the name within this agent and domain."""
        if not self.name:
            raise ValueError("Name is required to generate key")
        
        from .secret_key_generator import SecretKeyGenerator
        
        # Get existing keys for this agent and domain (excluding self if updating)
        existing_secrets = PersistentAgentSecret.objects.filter(
            agent=self.agent,
            domain_pattern=self.domain_pattern
        )
        if self.pk:
            existing_secrets = existing_secrets.exclude(pk=self.pk)
        
        existing_keys = set(existing_secrets.values_list('key', flat=True))
        
        return SecretKeyGenerator.generate_unique_key_from_name(self.name, existing_keys)

    def clean(self):
        """Validate the secret fields."""
        super().clean()
        
        # Validate domain pattern
        if self.domain_pattern:
            from .domain_validation import DomainPatternValidator
            try:
                DomainPatternValidator.validate_domain_pattern(self.domain_pattern)
                self.domain_pattern = DomainPatternValidator.normalize_domain_pattern(self.domain_pattern)
            except ValueError as e:
                raise ValidationError({'domain_pattern': str(e)})
        
        # Generate key from name if name is provided
        if self.name and self.agent:
            self.key = self.generate_key_from_name()
        
        # Validate secret key
        if self.key:
            from .domain_validation import DomainPatternValidator
            try:
                DomainPatternValidator._validate_secret_key(self.key)
            except ValueError as e:
                raise ValidationError({'key': str(e)})

    def set_value(self, value: str):
        """
        Encrypt and set the secret value.
        
        Args:
            value: Plain text secret value to encrypt
        """
        from .domain_validation import DomainPatternValidator
        
        # Validate the value before encryption
        DomainPatternValidator._validate_secret_value(value)
        
        # Encrypt the value
        from .encryption import SecretsEncryption
        self.encrypted_value = SecretsEncryption.encrypt_value(value)

    def get_value(self) -> str:
        """
        Decrypt and return the secret value.
        
        Returns:
            Plain text secret value
        """
        if not self.encrypted_value:
            return ""
        
        from .encryption import SecretsEncryption
        return SecretsEncryption.decrypt_value(self.encrypted_value)

    @property
    def is_requested(self) -> bool:
        """
        Check if this secret has been requested but doesn't have a value yet.
        
        Returns:
            True if the secret is requested, False otherwise
        """
        return self.requested

    def __str__(self):
        return f"Secret '{self.name}' ({self.key}) for {self.agent.name} on {self.domain_pattern}"


class PersistentAgentCommsEndpoint(models.Model):
    """Channel-agnostic communication endpoint (address/number/etc.)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="comms_endpoints",
    )
    channel = models.CharField(max_length=32, choices=CommsChannel.choices)
    address = models.CharField(max_length=512)
    is_primary = models.BooleanField(default=False)

    class Meta:
        unique_together = ("channel", "address")
        indexes = [
            models.Index(fields=["owner_agent", "channel"], name="pa_ep_agent_channel_idx"),
        ]
        ordering = ["channel", "address"]

    def __str__(self):
        return f"{self.channel}:{self.address}"


class CommsAllowlistEntry(models.Model):
    """Manual allowlist entry for agent communications (agent-level only)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="manual_allowlist",
        help_text="Agent to which this allowlist entry applies",
    )
    channel = models.CharField(max_length=32, choices=CommsChannel.choices)
    address = models.CharField(max_length=512, help_text="Email address or E.164 phone number")
    is_active = models.BooleanField(default=True)
    verified = models.BooleanField(
        default=True,
        help_text="Reserved for future use. Manual verification flag; currently not enforced."
    )
    allow_inbound = models.BooleanField(
        default=True,
        help_text="Whether this contact can send messages to the agent"
    )
    allow_outbound = models.BooleanField(
        default=True,
        help_text="Whether the agent can send messages to this contact"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "channel", "address"],
                name="uniq_allowlist_agent_channel_address",
            ),
        ]
        indexes = [
            models.Index(fields=["agent", "channel"], name="allow_agent_channel_idx"),
        ]
        ordering = ["channel", "address"]

    def clean(self):
        super().clean()

        # Normalize address
        if self.channel == CommsChannel.EMAIL:
            self.address = (self.address or "").strip().lower()
        else:
            self.address = (self.address or "").strip()
        
        # Restrict multi-player agents to email-only allowlists
        # Multi-player = org-owned agents OR agents with manual whitelist policy
        if self.channel == CommsChannel.SMS:
            # Check if agent is org-owned or uses manual whitelist (multi-player scenarios)
            if self.agent.organization_id is not None:
                raise ValidationError({
                    "channel": "Organization agents only support email addresses in allowlists. "
                               "Group SMS functionality is not yet available."
                })
            elif self.agent.whitelist_policy == PersistentAgent.WhitelistPolicy.MANUAL:
                raise ValidationError({
                    "channel": "Multi-player agents only support email addresses in allowlists. "
                               "Group SMS functionality is not yet available."
                })

        # Enforce per-agent cap on *active* entries and pending invitations (only when adding a new row)
        if self.is_active and self._state.adding:
            # Get the plan-based limit for this agent's owner
            from util.subscription_helper import get_user_max_contacts_per_agent
            cap = get_user_max_contacts_per_agent(self.agent.user)
            
            try:
                # Count both active entries and pending invitations
                active_count = (
                    CommsAllowlistEntry.objects
                    .filter(agent=self.agent, is_active=True)
                    .count()
                )
                
                # Also count pending invitations since they'll become active entries
                pending_count = (
                    AgentAllowlistInvite.objects
                    .filter(agent=self.agent, status=AgentAllowlistInvite.InviteStatus.PENDING)
                    .count()
                )
                
                total_count = active_count + pending_count
            except Exception as e:
                logger.error(
                    "Skipping allowlist cap check for agent %s due to error: %s",
                    self.agent_id, e
                )
                return

            if total_count >= cap:
                raise ValidationError({
                    "agent": (
                        f"Cannot add more contacts. Maximum {cap} contacts "
                        f"allowed per agent for your plan (including {pending_count} pending invitations)."
                    )
                })

    def __str__(self):
        return f"Allow<{self.channel}:{self.address}> for {self.agent_id}"


class AgentAllowlistInvite(models.Model):
    """Pending invitation for someone to join an agent's allowlist."""
    
    class InviteStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"  
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="allowlist_invites",
        help_text="Agent this invitation is for",
    )
    channel = models.CharField(max_length=32, choices=CommsChannel.choices)
    address = models.CharField(max_length=512, help_text="Email address or E.164 phone number")
    token = models.CharField(max_length=64, unique=True, help_text="Unique token for accept/reject URLs")
    status = models.CharField(max_length=16, choices=InviteStatus.choices, default=InviteStatus.PENDING)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_allowlist_invites",
        help_text="User who sent this invitation"
    )
    expires_at = models.DateTimeField(help_text="When this invitation expires")
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True, help_text="When they accepted/rejected")
    allow_inbound = models.BooleanField(default=True)
    allow_outbound = models.BooleanField(default=True)
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "channel", "address"],
                condition=models.Q(status__in=["pending", "accepted"]),
                name="uniq_active_allowlist_invite",
            ),
        ]
        indexes = [
            models.Index(fields=["token"], name="allow_invite_token_idx"),
            models.Index(fields=["agent", "status"], name="allow_invite_agent_status_idx"),
        ]
        ordering = ["-created_at"]
    
    def clean(self):
        super().clean()
        # Normalize address like CommsAllowlistEntry
        if self.channel == CommsChannel.EMAIL:
            self.address = (self.address or "").strip().lower()
        else:
            self.address = (self.address or "").strip()
        
        # Check contact limit when creating new invitation
        if self._state.adding and self.status == self.InviteStatus.PENDING:
            # Get the plan-based limit for this agent's owner
            from util.subscription_helper import get_user_max_contacts_per_agent
            cap = get_user_max_contacts_per_agent(self.agent.user)
            
            try:
                # Count both active entries and pending invitations
                active_count = (
                    CommsAllowlistEntry.objects
                    .filter(agent=self.agent, is_active=True)
                    .count()
                )
                
                # Count existing pending invitations (not including this one since it's being added)
                pending_count = (
                    AgentAllowlistInvite.objects
                    .filter(agent=self.agent, status=self.InviteStatus.PENDING)
                    .count()
                )
                
                total_count = active_count + pending_count
            except Exception as e:
                logger.error(
                    "Skipping invitation cap check for agent %s due to error: %s",
                    self.agent_id, e
                )
                return
            
            if total_count >= cap:
                raise ValidationError({
                    "agent": (
                        f"Cannot send more invitations. Maximum {cap} contacts "
                        f"allowed per agent for your plan (currently {active_count} active, {pending_count} pending)."
                    )
                })
    
    def is_expired(self):
        """Check if this invitation has expired."""
        return timezone.now() > self.expires_at
    
    def can_be_accepted(self):
        """Check if this invitation can still be accepted."""
        return self.status == self.InviteStatus.PENDING and not self.is_expired()
    
    def accept(self):
        """Accept this invitation and create the allowlist entry."""
        if not self.can_be_accepted():
            raise ValueError("This invitation cannot be accepted")
        
        # Create the allowlist entry
        entry, created = CommsAllowlistEntry.objects.get_or_create(
            agent=self.agent,
            channel=self.channel,
            address=self.address,
            defaults={
                "is_active": True,
                "allow_inbound": self.allow_inbound,
                "allow_outbound": self.allow_outbound,
            }
        )
        
        # Switch agent to manual allowlist mode if not already
        # This ensures the agent respects the allowlist once someone accepts an invitation
        if self.agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
            self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
            self.agent.save(update_fields=['whitelist_policy'])
        
        # Mark invitation as accepted
        self.status = self.InviteStatus.ACCEPTED
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "responded_at"])
        
        return entry
    
    def reject(self):
        """Reject this invitation."""
        if self.status != self.InviteStatus.PENDING:
            raise ValueError("This invitation has already been responded to")
        
        self.status = self.InviteStatus.REJECTED
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "responded_at"])
    
    def __str__(self):
        return f"Invite<{self.channel}:{self.address}> for {self.agent.name} ({self.status})"


class CommsAllowlistRequest(models.Model):
    """Request from agent to add a contact to allowlist."""
    
    class RequestStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="contact_requests",
        help_text="Agent requesting contact permission"
    )
    channel = models.CharField(max_length=32, choices=CommsChannel.choices)
    address = models.CharField(max_length=512, help_text="Email address or E.164 phone number")
    
    # Request metadata
    name = models.CharField(
        max_length=256, 
        blank=True,
        help_text="Contact's name if known"
    )
    reason = models.TextField(help_text="Why the agent needs to contact this person")
    purpose = models.CharField(
        max_length=512, 
        help_text="Brief purpose of communication (e.g., 'Schedule meeting', 'Get approval')"
    )
    
    # Direction settings for the request
    request_inbound = models.BooleanField(
        default=True,
        help_text="Agent is requesting to receive messages from this contact"
    )
    request_outbound = models.BooleanField(
        default=True,
        help_text="Agent is requesting to send messages to this contact"
    )
    
    # Status tracking
    status = models.CharField(
        max_length=16, 
        choices=RequestStatus.choices, 
        default=RequestStatus.PENDING
    )
    
    # Timestamps
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True, 
        blank=True,
        help_text="Optional expiry for this request"
    )
    
    # Link to created invitation if approved
    allowlist_invitation = models.ForeignKey(
        "AgentAllowlistInvite",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="from_request",
        help_text="Invitation created when request was approved"
    )
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "channel", "address"],
                condition=models.Q(status="pending"),
                name="uniq_pending_contact_request",
            ),
        ]
        indexes = [
            models.Index(fields=["agent", "status"], name="contact_req_agent_status_idx"),
            models.Index(fields=["requested_at"], name="contact_req_requested_idx"),
        ]
        ordering = ["-requested_at"]
    
    def clean(self):
        super().clean()
        # Normalize address like CommsAllowlistEntry
        if self.channel == CommsChannel.EMAIL:
            self.address = (self.address or "").strip().lower()
        else:
            self.address = (self.address or "").strip()
    
    def is_expired(self):
        """Check if this request has expired."""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at
    
    def can_be_approved(self):
        """Check if this request can still be approved."""
        return self.status == self.RequestStatus.PENDING and not self.is_expired()
    
    def approve(self, invited_by, skip_limit_check=False, skip_invitation=True):
        """Approve this request by creating an invitation or direct allowlist entry.
        
        Args:
            invited_by: User approving the request
            skip_limit_check: Skip validation of contact limits
            skip_invitation: If True, directly create allowlist entry instead of invitation
        """
        import secrets
        from datetime import timedelta
        
        if not self.can_be_approved():
            raise ValueError("This request cannot be approved")
        
        # Check if contact already exists in allowlist
        existing_entry = CommsAllowlistEntry.objects.filter(
            agent=self.agent,
            channel=self.channel,
            address=self.address,
            is_active=True
        ).first()
        
        if existing_entry:
            # Already in allowlist, just mark as approved
            # But still switch to manual mode if needed
            if self.agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
                self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
                self.agent.save(update_fields=['whitelist_policy'])
            
            self.status = self.RequestStatus.APPROVED
            self.responded_at = timezone.now()
            self.save(update_fields=["status", "responded_at"])
            return existing_entry
        
        # If skip_invitation is True, directly create the allowlist entry
        if skip_invitation:
            # Create the allowlist entry directly with requested direction settings
            entry = CommsAllowlistEntry.objects.create(
                agent=self.agent,
                channel=self.channel,
                address=self.address,
                is_active=True,
                allow_inbound=self.request_inbound,
                allow_outbound=self.request_outbound
            )
            
            # Switch agent to manual allowlist mode if not already
            if self.agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
                self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
                self.agent.save(update_fields=['whitelist_policy'])
            
            # Mark request as approved
            self.status = self.RequestStatus.APPROVED
            self.responded_at = timezone.now()
            self.save(update_fields=["status", "responded_at"])
            
            return entry
        
        # Original invitation flow (kept for backwards compatibility)
        # Check if invitation already exists and is pending
        existing_invite = AgentAllowlistInvite.objects.filter(
            agent=self.agent,
            channel=self.channel,
            address=self.address,
            status=AgentAllowlistInvite.InviteStatus.PENDING
        ).first()
        
        if existing_invite:
            # Invitation already pending, just mark request as approved
            # But still switch to manual mode if needed
            if self.agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
                self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
                self.agent.save(update_fields=['whitelist_policy'])
            
            self.status = self.RequestStatus.APPROVED
            self.responded_at = timezone.now()
            self.allowlist_invitation = existing_invite
            self.save(update_fields=["status", "responded_at", "allowlist_invitation"])
            return existing_invite
        
        # Create new invitation
        invitation = AgentAllowlistInvite(
            agent=self.agent,
            channel=self.channel,
            address=self.address,
            token=secrets.token_urlsafe(32),
            invited_by=invited_by,
            allow_inbound=self.request_inbound,
            allow_outbound=self.request_outbound,
            expires_at=timezone.now() + timedelta(days=7)
        )
        
        # Check limits unless explicitly skipped
        if not skip_limit_check:
            try:
                invitation.full_clean()
            except ValidationError:
                raise
        
        invitation.save()
        
        # Switch agent to manual allowlist mode if not already
        # This ensures the agent respects the allowlist once a contact request is approved
        if self.agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
            self.agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
            self.agent.save(update_fields=['whitelist_policy'])
        
        # Mark request as approved and link to invitation
        self.status = self.RequestStatus.APPROVED
        self.responded_at = timezone.now()
        self.allowlist_invitation = invitation
        self.save(update_fields=["status", "responded_at", "allowlist_invitation"])
        
        return invitation
    
    def reject(self):
        """Reject this request."""
        if self.status != self.RequestStatus.PENDING:
            raise ValueError("This request has already been responded to")
        
        self.status = self.RequestStatus.REJECTED
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "responded_at"])
    
    def __str__(self):
        return f"ContactRequest<{self.channel}:{self.address}> for {self.agent.name} ({self.status})"


class PersistentAgentEmailEndpoint(models.Model):
    """Email-specific metadata for an endpoint."""

    endpoint = models.OneToOneField(
        PersistentAgentCommsEndpoint,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="email_meta",
    )
    display_name = models.CharField(max_length=256, blank=True)
    verified = models.BooleanField(default=False)

    def __str__(self):
        return f"EmailEndpoint<{self.endpoint.address}>"


class AgentEmailAccount(models.Model):
    """Per-agent email account for BYO SMTP/IMAP.

    One-to-one with an agent-owned email endpoint. SMTP used for outbound in
    Phase 1; IMAP config stored for Phase 2.
    """

    class SmtpSecurity(models.TextChoices):
        SSL = "ssl", "SSL"
        STARTTLS = "starttls", "STARTTLS"
        NONE = "none", "None"

    class AuthMode(models.TextChoices):
        NONE = "none", "None"
        PLAIN = "plain", "PLAIN"
        LOGIN = "login", "LOGIN"

    class ImapSecurity(models.TextChoices):
        SSL = "ssl", "SSL"
        STARTTLS = "starttls", "STARTTLS"
        NONE = "none", "None"

    endpoint = models.OneToOneField(
        PersistentAgentCommsEndpoint,
        on_delete=models.CASCADE,
        related_name="agentemailaccount",
        primary_key=True,
    )

    # SMTP (outbound)
    smtp_host = models.CharField(max_length=255, blank=True)
    smtp_port = models.PositiveIntegerField(null=True, blank=True)
    smtp_security = models.CharField(
        max_length=16, choices=SmtpSecurity.choices, default=SmtpSecurity.STARTTLS
    )
    smtp_auth = models.CharField(
        max_length=16, choices=AuthMode.choices, default=AuthMode.LOGIN
    )
    smtp_username = models.CharField(max_length=255, blank=True)
    smtp_password_encrypted = models.BinaryField(null=True, blank=True)
    is_outbound_enabled = models.BooleanField(default=False, db_index=True)

    # IMAP (inbound) — Phase 2
    imap_host = models.CharField(max_length=255, blank=True)
    imap_port = models.PositiveIntegerField(null=True, blank=True)
    imap_security = models.CharField(
        max_length=16, choices=ImapSecurity.choices, default=ImapSecurity.SSL
    )
    imap_username = models.CharField(max_length=255, blank=True)
    imap_password_encrypted = models.BinaryField(null=True, blank=True)
    imap_folder = models.CharField(max_length=128, default="INBOX")
    is_inbound_enabled = models.BooleanField(default=False)
    # Optional per-account toggle to enable IDLE watchers for lower latency (keeps polling as source of truth)
    imap_idle_enabled = models.BooleanField(default=False)

    poll_interval_sec = models.PositiveIntegerField(default=120)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_seen_uid = models.CharField(max_length=64, blank=True)
    backoff_until = models.DateTimeField(null=True, blank=True)

    # Health
    connection_last_ok_at = models.DateTimeField(null=True, blank=True)
    connection_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_outbound_enabled"], name="agent_email_outbound_idx"),
            models.Index(fields=["endpoint"], name="agent_email_endpoint_idx"),
        ]
        ordering = ["-updated_at"]

    def __str__(self):
        owner = getattr(self.endpoint, "owner_agent", None)
        return f"AgentEmailAccount<{self.endpoint.address}> for {getattr(owner, 'name', 'unknown')}"

    # Convenience accessors
    def get_smtp_password(self) -> str:
        from .encryption import SecretsEncryption
        try:
            return SecretsEncryption.decrypt_value(self.smtp_password_encrypted) if self.smtp_password_encrypted else ""
        except Exception:
            return ""

    def set_smtp_password(self, value: str) -> None:
        from .encryption import SecretsEncryption
        self.smtp_password_encrypted = SecretsEncryption.encrypt_value(value)

    def get_imap_password(self) -> str:
        from .encryption import SecretsEncryption
        try:
            return SecretsEncryption.decrypt_value(self.imap_password_encrypted) if self.imap_password_encrypted else ""
        except Exception:
            return ""

    def set_imap_password(self, value: str) -> None:
        from .encryption import SecretsEncryption
        self.imap_password_encrypted = SecretsEncryption.encrypt_value(value)

    def clean(self):
        super().clean()
        # Endpoint must be agent-owned email
        if self.endpoint is None:
            raise ValidationError({"endpoint": "Endpoint is required."})
        if self.endpoint.channel != CommsChannel.EMAIL:
            raise ValidationError({"endpoint": "AgentEmailAccount must be attached to an email endpoint."})
        if self.endpoint.owner_agent_id is None:
            raise ValidationError({"endpoint": "Only agent-owned endpoints may have SMTP/IMAP accounts."})

        # If enabling outbound, ensure required SMTP fields are present
        if self.is_outbound_enabled:
            missing: list[str] = []
            for field in ("smtp_host", "smtp_port", "smtp_security", "smtp_auth"):
                if not getattr(self, field):
                    missing.append(field)
            if missing:
                raise ValidationError({f: "Required when outbound is enabled" for f in missing})

            if self.smtp_auth != self.AuthMode.NONE:
                if not self.smtp_username:
                    raise ValidationError({"smtp_username": "Username required for authenticated SMTP"})
                if not self.smtp_password_encrypted:
                    raise ValidationError({"smtp_password_encrypted": "Password required for authenticated SMTP"})

            # Gate: require a successful connection test before enabling
            if not self.connection_last_ok_at:
                raise ValidationError({
                    "is_outbound_enabled": "Run Test SMTP and ensure success before enabling outbound."
                })


class PersistentAgentSmsEndpoint(models.Model):
    """SMS-specific metadata for an endpoint."""

    endpoint = models.OneToOneField(
        PersistentAgentCommsEndpoint,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="sms_meta",
    )
    carrier_name = models.CharField(max_length=128, blank=True)
    supports_mms = models.BooleanField(default=False)

    def __str__(self):
        return f"SmsEndpoint<{self.endpoint.address}>"


class PersistentAgentConversation(models.Model):
    """A logical conversation / thread across any channel."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.CharField(max_length=32, choices=CommsChannel.choices)
    address = models.CharField(max_length=512)
    display_name = models.CharField(max_length=256, blank=True)
    owner_agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="owned_conversations",
    )

    class Meta:
        indexes = [
            models.Index(fields=["channel", "address"], name="pa_conv_channel_addr_idx"),
        ]
        ordering = ["-id"]

    def __str__(self):
        return f"Conversation<{self.channel}:{self.address}>"


class PersistentAgentConversationParticipant(models.Model):
    """Members participating in a conversation."""

    class ParticipantRole(models.TextChoices):
        AGENT = "agent", "Agent"
        HUMAN_USER = "human_user", "Human User"
        EXTERNAL = "external", "External"

    conversation = models.ForeignKey(
        PersistentAgentConversation,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    endpoint = models.ForeignKey(
        PersistentAgentCommsEndpoint,
        on_delete=models.CASCADE,
        related_name="conversation_memberships",
    )
    role = models.CharField(max_length=16, choices=ParticipantRole.choices)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("conversation", "endpoint")
        indexes = [
            models.Index(fields=["endpoint", "conversation"], name="pa_part_ep_conv_idx"),
        ]

    def __str__(self):
        return f"{self.role} {self.endpoint} in {self.conversation}"


class PersistentAgentMessage(models.Model):
    """Normalized message across any channel or conversation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Switched from autoincrement bigint to ULID string (26 chars, lexicographically time-ordered)
    seq = models.CharField(
        max_length=26,
        unique=True,
        editable=False,
        db_index=True,
        default=generate_ulid,
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    is_outbound = models.BooleanField()

    from_endpoint = models.ForeignKey(
        PersistentAgentCommsEndpoint,
        on_delete=models.CASCADE,
        related_name="messages_sent",
    )
    to_endpoint = models.ForeignKey(
        PersistentAgentCommsEndpoint,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="messages_received",
    )
    cc_endpoints = models.ManyToManyField(
        PersistentAgentCommsEndpoint,
        related_name="cc_messages",
        blank=True,
        help_text="CC recipients for email or additional recipients for group SMS",
    )
    conversation = models.ForeignKey(
        PersistentAgentConversation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="messages",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )

    # Denormalized pointer for efficient history queries
    owner_agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="agent_messages",
        help_text="The persistent agent this message ultimately belongs to (derived from conversation or endpoint)",
    )

    body = models.TextField()
    raw_payload = models.JSONField(default=dict, blank=True)

    # Delivery-tracking fields (NEW)
    latest_status = models.CharField(
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.QUEUED,
        db_index=True,
    )
    latest_sent_at = models.DateTimeField(null=True, blank=True)
    latest_delivered_at = models.DateTimeField(null=True, blank=True)
    latest_error_code = models.CharField(max_length=64, blank=True)
    latest_error_message = models.CharField(max_length=256, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation", "-seq"], name="pa_msg_conv_seq_idx"),
            models.Index(fields=["conversation", "-timestamp"], name="pa_msg_conv_ts_idx"),
            models.Index(fields=["from_endpoint", "to_endpoint", "-seq"], name="pa_msg_endpoints_seq_idx"),
            models.Index(fields=["from_endpoint", "-timestamp"], name="pa_msg_from_ts_idx"),
            models.Index(fields=["owner_agent", "-timestamp"], name="pa_msg_agent_ts_idx"),
            models.Index(fields=["latest_status"], name="pa_msg_latest_status_idx"),
        ]
        ordering = ["-seq"]

    def clean(self):
        super().clean()
        # Validation: exactly one of to_endpoint XOR conversation must be set.
        if bool(self.to_endpoint) == bool(self.conversation):
            raise ValidationError(
                "Exactly one of 'to_endpoint' or 'conversation' must be set (not both)."
            )

    def __str__(self):
        direction = "OUT" if self.is_outbound else "IN"
        preview = (self.body or "")[:40]
        return f"MSG[{self.seq}] {direction} {preview}..."

    def save(self, *args, **kwargs):
        """Persist message and auto-fill denormalised owner pointer.

        Sequence (`seq`) is now generated automatically via ULID default, so we
        only need to ensure the owner_agent back-reference is set.
        """

        # Auto-populate owner_agent if missing for denormalization & index use
        if self.owner_agent_id is None:
            if self.conversation and self.conversation.owner_agent_id:
                self.owner_agent = self.conversation.owner_agent
            elif self.from_endpoint and self.from_endpoint.owner_agent_id:
                self.owner_agent = self.from_endpoint.owner_agent

        super().save(*args, **kwargs)


class PersistentAgentMessageAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        PersistentAgentMessage,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to="agent_attachments/%Y/%m/%d/")
    content_type = models.CharField(max_length=128)
    file_size = models.PositiveBigIntegerField()
    filename = models.CharField(max_length=512)
    filespace_node = models.ForeignKey(
        "AgentFsNode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_attachments",
        help_text="If imported to a filespace, the created AgentFsNode this attachment maps to.",
    )

    def __str__(self):
        return f"Attachment({self.filename})"


class PersistentAgentStep(models.Model):
    """A single action taken by a PersistentAgent (tool call, internal reasoning, etc.)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Parent agent
    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="steps",
        help_text="The persistent agent that executed this step",
    )

    # Credit used for this step
    task_credit = models.ForeignKey(
        "TaskCredit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_steps",
    )

    # Free-form narrative or data for non-tool steps
    description = models.TextField(
        blank=True,
        help_text="Narrative or raw content describing what happened in this step.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # Token usage tracking fields
    prompt_tokens = models.IntegerField(
        null=True, 
        blank=True,
        help_text="Number of tokens used in the prompt for this step's LLM call"
    )
    completion_tokens = models.IntegerField(
        null=True,
        blank=True, 
        help_text="Number of tokens generated in the completion for this step's LLM call"
    )
    total_tokens = models.IntegerField(
        null=True,
        blank=True,
        help_text="Total tokens used (prompt + completion) for this step's LLM call"
    )
    # Credits charged for this step (for audit). If not provided, defaults to configured per‑task cost.
    credits_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Credits charged for this step; defaults to configured per‑task cost.",
    )
    cached_tokens = models.IntegerField(
        null=True,
        blank=True,
        help_text="Number of cached tokens used (if provider supports caching)"
    )
    llm_model = models.CharField(
        max_length=256,
        null=True,
        blank=True,
        help_text="LLM model used for this step (e.g., 'claude-3-opus-20240229')"
    )
    llm_provider = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="LLM provider used for this step (e.g., 'anthropic', 'openai')"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Fast lookup of recent steps for an agent
            models.Index(fields=["agent", "-created_at"], name="pa_step_recent_idx"),
            # Ascending order index to support compaction filter/order queries
            models.Index(fields=["agent", "created_at"], name="pa_step_agent_ts_idx"),
        ]

    def __str__(self):
        preview = (self.description or "").replace("\n", " ")[:60]
        return f"Step {preview}..."

    def save(self, *args, **kwargs):
        # On creation, optionally consume credits for chargeable steps only.
        if self._state.adding:
            from django.core.exceptions import ValidationError
            from django.conf import settings as dj_settings
            # Determine owner: organization if agent is org-owned; otherwise the agent's user
            owner = None
            if self.agent and getattr(self.agent, 'organization', None):
                owner = self.agent.organization
            elif self.agent:
                owner = self.agent.user

            # Heuristic: only charge credits for LLM/tool compute steps – indicated by either
            # an explicit credits_cost override or presence of token/model usage fields.
            chargeable = (
                self.credits_cost is not None
                or self.llm_model is not None
                or self.prompt_tokens is not None
                or self.total_tokens is not None
            )

            if owner is not None and chargeable:
                amount = self.credits_cost if self.credits_cost is not None else dj_settings.CREDITS_PER_TASK
                result = TaskCreditService.check_and_consume_credit_for_owner(owner, amount=amount)

                if not result.get('success'):
                    raise ValidationError({"quota": result.get('error_message')})

                self.task_credit = result.get('credit')
                if self.credits_cost is None:
                    self.credits_cost = amount

        return super().save(*args, **kwargs)


class PersistentAgentToolCall(models.Model):
    """Details for a step that involved invoking an external / internal tool."""

    # Re-use the Step's PK to keep a strict 1-1 relationship
    step = models.OneToOneField(
        "PersistentAgentStep",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="tool_call",
    )

    tool_name = models.CharField(max_length=256)
    tool_params = models.JSONField(null=True, blank=True)
    result = models.TextField(blank=True, help_text="Raw result or output from the tool call (may be large)")

    class Meta:
        ordering = ["-step__created_at"]  # newest first via step timestamp
        indexes = [
            models.Index(fields=["tool_name"], name="pa_tool_name_idx"),
        ]

    def __str__(self):
        preview = (self.result or "").replace("\n", " ")[:60]
        return f"ToolCall<{self.tool_name}> {preview}..."


class PersistentAgentCronTrigger(models.Model):
    """Denotes that a step was created due to a scheduled cron execution."""

    step = models.OneToOneField(
        "PersistentAgentStep",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="cron_trigger",
    )

    cron_expression = models.CharField(
        max_length=128,
        help_text="Cron expression that scheduled this execution (captured at trigger time)",
    )

    class Meta:
        ordering = ["-step__created_at"]
        indexes = [
            models.Index(fields=["cron_expression"], name="pa_cron_expr_idx"),
        ]

    def __str__(self):
        return f"CronTrigger<{self.cron_expression}> at {self.step.created_at}"


class PersistentAgentCommsSnapshot(models.Model):
    """Materialized summary of all communications for an agent up to a given moment.

    Snapshots are generated incrementally: each snapshot summarizes everything up to
    `snapshot_until` by combining the previous snapshot (if any) with messages since
    that timestamp.  Only model structure is defined here; generation logic lives
    elsewhere.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="comms_snapshots",
    )

    # Link to the previous snapshot for incremental generation (optional for the first snapshot)
    previous_snapshot = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="next_snapshot",
    )

    # All messages with timestamp <= snapshot_until are represented in `summary`
    snapshot_until = models.DateTimeField(help_text="Inclusive upper bound of message timestamps represented in this snapshot")

    # The actual summarized content (could be text, markdown, JSON, etc.)
    summary = models.TextField(help_text="Agent-readable or machine-readable summary of communications up to snapshot_until")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-snapshot_until"]
        constraints = [
            # Prevent two snapshots at the same cut-off for a single agent
            models.UniqueConstraint(fields=["agent", "snapshot_until"], name="unique_agent_snapshot_until"),
        ]
        indexes = [
            # Quickly fetch latest snapshot for an agent
            models.Index(fields=["agent", "-snapshot_until"], name="pa_snapshot_recent_idx"),
        ]

    def __str__(self):
        return f"CommsSnapshot<{self.agent.name}> to {self.snapshot_until.isoformat()}"


class PersistentAgentStepSnapshot(models.Model):
    """Materialized summary of all agent *steps* up to a specific time.

    Like the comms snapshot, this is built incrementally using the previous
    snapshot plus all steps executed after that cut-off.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="step_snapshots",
    )

    previous_snapshot = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="next_snapshot",
    )

    snapshot_until = models.DateTimeField(help_text="Inclusive upper bound of step.created_at values represented in this snapshot")

    summary = models.TextField(help_text="Summary of agent steps up to snapshot_until")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-snapshot_until"]
        constraints = [
            models.UniqueConstraint(fields=["agent", "snapshot_until"], name="unique_agent_step_snapshot_until"),
        ]
        indexes = [
            models.Index(fields=["agent", "-snapshot_until"], name="pa_step_snapshot_recent_idx"),
        ]

    def __str__(self):
        return f"StepSnapshot<{self.agent.name}> to {self.snapshot_until.isoformat()}"


class PersistentAgentSystemStep(models.Model):
    """Denotes that a step was created by an **internal system process** (scheduler, snapshotter, etc.).

    Mirrors `PersistentAgentCronTrigger`, keeping the audit model parallel to
    `PersistentAgentToolCall` and `PersistentAgentCronTrigger`.  A step gets
    one — and only one — satellite record, so we reuse the PK via a
    OneToOneField.
    """

    class Code(models.TextChoices):
        PROCESS_EVENTS = "PROCESS_EVENTS", "Process Events"
        SNAPSHOT = "SNAPSHOT", "Snapshot"
        CREDENTIALS_PROVIDED = "CREDENTIALS_PROVIDED", "Credentials Provided"
        CONTACTS_APPROVED = "CONTACTS_APPROVED", "Contacts Approved"
        # Add more system-generated step codes here as needed.

    step = models.OneToOneField(
        "PersistentAgentStep",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="system_step",
    )

    code = models.CharField(max_length=64, choices=Code.choices)
    notes = models.TextField(blank=True, help_text="Optional free-form notes for debugging / context")

    class Meta:
        ordering = ["-step__created_at"]
        indexes = [
            models.Index(fields=["code"], name="pa_sys_code_idx"),
        ]

    def __str__(self):
        preview = (self.notes or "").replace("\n", " ")[:60]
        return f"SystemStep<{self.code}> {preview}..."


class OutboundMessageAttempt(models.Model):
    """Append-only log of every delivery or retry attempt for an outbound message."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        PersistentAgentMessage,
        on_delete=models.CASCADE,
        related_name="attempts",
    )

    provider = models.CharField(max_length=32)
    provider_message_id = models.CharField(max_length=128, blank=True, db_index=True)

    status = models.CharField(
        max_length=16,
        choices=DeliveryStatus.choices,
        db_index=True,
    )
    queued_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-queued_at"]
        indexes = [
            models.Index(fields=["status", "-queued_at"], name="msg_attempt_status_idx"),
            models.Index(fields=["provider_message_id"], name="msg_attempt_provider_id_idx"),
            models.Index(fields=["provider"], name="msg_attempt_provider_idx"),
        ]

    def __str__(self):
        preview = (self.error_message or "")[:40]
        return f"Attempt<{self.provider}|{self.status}> {preview}..."


class UsageThresholdSent(models.Model):
    """
    One row per (user, calendar month, threshold) that has already triggered
    a task‑usage notice.  Presence of the row = email/event has been sent.
    """

    # ------------------------------------------------------------------ PK/uniqueness
    user        = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_index=True,
        help_text="User who crossed the threshold.",
    )
    period_ym   = models.CharField(
        max_length=6,
        help_text="Billing month in 'YYYYMM' format (e.g. '202507').",
    )
    threshold   = models.PositiveSmallIntegerField(
        help_text="Integer percent of quota crossed (75, 90, 100).",
    )

    # ------------------------------------------------------------------ metadata
    sent_at     = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when we first emitted the threshold event.",
    )
    plan_limit  = models.PositiveIntegerField(
        help_text="Task quota that applied at the time of the event (100 or 500).",
    )

    # ------------------------------------------------------------------ Django meta
    class Meta:
        # Composite uniqueness => INSERT - ON CONFLICT DO NOTHING is safe
        constraints = [
            models.UniqueConstraint(
                fields=["user", "period_ym", "threshold"],
                name="unique_user_month_threshold",
            ),
        ]
        # Helpful for admin list filters and ORM ordering
        ordering = ["-sent_at"]

    def __str__(self) -> str:
        return (
            f"{self.user_id} • {self.period_ym} • {self.threshold}% "
            f"(plan_limit={self.plan_limit})"
        )

class SmsNumber(models.Model):
    """
    Represents a phone number that can be used for SMS communication.
    This is a simple model to store phone numbers with basic metadata.

    Note: Twilio is currently the only supported provider, but this model
    is designed to be extensible for future SMS providers.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sid = models.CharField(  # PNxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        max_length=34, unique=True
    )
    phone_number = models.CharField(max_length=15, unique=True, help_text="The phone number in E.164 format (e.g., +1234567890)")
    friendly_name = models.CharField(max_length=64, blank=True)
    country = models.CharField(max_length=2)
    region = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    provider = models.CharField(
        max_length=64,
        blank=False,
        choices=SmsProvider.choices,
        default=SmsProvider.TWILIO,
        help_text="Optional provider name for the SMS service (e.g., Twilio)"
    )
    is_sms_enabled = models.BooleanField(default=True)
    is_mms_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, help_text="Whether this number is currently active and can be used for sending/receiving messages")
    released_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when this number was released (if applicable)")
    last_synced_at = models.DateTimeField(auto_now=True)   # updates on each sync
    extra = models.JSONField(default=dict, blank=True)     # raw Twilio attrs
    messaging_service_sid = models.CharField(
        max_length=34,  # “MG” + 32-char SID
        blank=True,  # keep nullable if some numbers aren’t in a service
        null=True,
        db_index=True,  # handy if you’ll query by service often
    )


    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"SmsNumber<{self.phone_number}> ({self.provider})"


def _generate_short_code(length: int = 6) -> str:
    """Generate an alphabetic short code."""
    if length < 3:
        length = 3
    chars = string.ascii_letters
    return "".join(secrets.choice(chars) for _ in range(length))


class LinkShortener(models.Model):
    """Map a short alphabetic code to a full URL."""

    code_validator = RegexValidator(
        regex=r"^[A-Za-z]{3,}$",
        message="Code must be at least three alphabetic characters.",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(
        max_length=32,
        unique=True,
        blank=True,
        validators=[code_validator],
        help_text="Short code used in the redirect URL.",
    )
    url = models.URLField(
        help_text="Destination URL",
        max_length=2048
    )
    hits = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="link_shorteners",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )

    def save(self, *args, **kwargs):
        if self.code:
            super().save(*args, **kwargs)
            return

        from django.db import IntegrityError

        for _ in range(10):  # Limit retries
            self.code = _generate_short_code()
            try:
                super().save(*args, **kwargs)
                return
            except IntegrityError:
                # Collision, try again
                continue

        raise RuntimeError("Could not generate a unique short code.")

    def increment_hits(self) -> None:
        LinkShortener.objects.filter(pk=self.pk).update(hits=models.F("hits") + 1)

    def get_absolute_url(self) -> str:
        """Return the full URL for this short code."""
        from django.urls import reverse
        return reverse("short_link", kwargs={"code": self.code})

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.code} -> {self.url}"


# --------------------------------------------------------------------
# Agent Filesystem (Working Set) Models
# --------------------------------------------------------------------

def agent_fs_upload_to(instance: "AgentFsNode", filename: str) -> str:
    """
    Stable object-store key:
    agent_fs/<filespace_uuid>/<node_uuid>/<sanitized_original_filename>
    """
    safe = get_valid_filename(os.path.basename(filename or "file"))
    return f"agent_fs/{instance.filespace_id}/{instance.id}/{safe}"


class AgentFileSpace(models.Model):
    """
    A logical filesystem root that can be mounted by one or more PersistentAgents.
    Keeps things future-proof for sharing a working set across agents.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, help_text="Human-friendly name for this filespace")
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_filespaces",
        help_text="Owning user; access for agents is managed via the access table.",
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    agents = models.ManyToManyField(
        "PersistentAgent",
        through="AgentFileSpaceAccess",
        related_name="filespaces",
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner_user", "-created_at"], name="afs_owner_recent_idx"),
            models.Index(fields=["name"], name="afs_name_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["owner_user", "name"], name="unique_filespace_per_user_name")
        ]

    def __str__(self) -> str:
        return f"FileSpace<{self.name}> ({self.id})"


class AgentFileSpaceAccess(models.Model):
    """
    Access control linking agents to filespaces.
    Keeps it simple: role is OWNER / WRITER / READER.
    """
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        WRITER = "WRITER", "Writer"
        READER = "READER", "Reader"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filespace = models.ForeignKey(AgentFileSpace, on_delete=models.CASCADE, related_name="access")
    agent = models.ForeignKey(PersistentAgent, on_delete=models.CASCADE, related_name="filespace_access")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.OWNER)
    is_default = models.BooleanField(
        default=False,
        help_text="Whether this is the agent's default working-set filespace."
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-granted_at"]
        constraints = [
            models.UniqueConstraint(fields=["filespace", "agent"], name="unique_agent_filespace_access"),
            models.UniqueConstraint(
                fields=["agent"],
                condition=models.Q(is_default=True),
                name="unique_default_filespace_per_agent",
            ),
        ]
        indexes = [
            models.Index(fields=["agent", "is_default"], name="afs_access_default_idx"),
            models.Index(fields=["filespace", "role"], name="afs_access_role_idx"),
        ]

    def __str__(self) -> str:
        return f"Access<{self.agent.name}→{self.filespace.name}:{self.role}>"


class AgentFsNodeQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(is_deleted=False)

    def directories(self):
        return self.filter(node_type=AgentFsNode.NodeType.DIR)

    def files(self):
        return self.filter(node_type=AgentFsNode.NodeType.FILE)

    def in_dir(self, parent: "AgentFsNode | None"):
        return self.filter(parent=parent)


class AgentFsNode(models.Model):
    """
    Single, unified node type for both directories and files.

    Design principles:
    - Adjacency list (parent pointer) + cached 'path' for human-readable path.
    - Object store key is stable (based on node UUID) and independent of name/moves.
    - Unique name per directory, case-sensitive (simple & predictable).
    - Efficient listing via (filespace, parent) index; traversal via parent chain.
    """
    class NodeType(models.TextChoices):
        DIR = "dir", "Directory"
        FILE = "file", "File"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    filespace = models.ForeignKey(
        AgentFileSpace,
        on_delete=models.CASCADE,
        related_name="nodes",
        help_text="The filesystem root this node belongs to.",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        help_text="Parent directory; null means the node is at the filespace root.",
    )
    node_type = models.CharField(max_length=8, choices=NodeType.choices)

    # Display name (what users see). For files, include extension here.
    name = models.CharField(max_length=255, help_text="Directory or file name (no path separators)")

    # Cached human-readable path (e.g., '/foo/bar/baz.txt'). Updated on rename/move.
    path = models.TextField(
        blank=True,
        help_text="Cached absolute path within the filespace for quick lookups and UI."
    )

    # Binary content (only for FILE nodes). Stored via Django Storage (GCS in prod, MinIO locally).
    content = models.FileField(
        upload_to=agent_fs_upload_to,
        null=True,
        blank=True,
        help_text="Binary content for files. Empty for directories."
    )

    # Metadata (files only; optional precomputed values)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    mime_type = models.CharField(max_length=127, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True)

    created_by_agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_nodes",
        help_text="Agent that created this node, if applicable."
    )

    # Soft delete (trash) support
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AgentFsNodeQuerySet.as_manager()

    class Meta:
        ordering = ["node_type", "name"]  # dirs then files (since 'dir' < 'file'), then alpha name
        constraints = [
            # Unique name within a directory in a given filespace (excluding deleted nodes)
            models.UniqueConstraint(
                fields=["filespace", "parent", "name"],
                condition=models.Q(is_deleted=False),
                name="unique_name_per_directory"
            ),
            # Unique name for root-level nodes (where parent IS NULL and not deleted)
            models.UniqueConstraint(
                fields=["filespace", "name"],
                condition=models.Q(parent__isnull=True, is_deleted=False),
                name="unique_name_per_filespace_root"
            ),

        ]
        indexes = [
            models.Index(fields=["filespace", "parent", "node_type", "name"], name="fs_list_idx"),
            models.Index(fields=["filespace", "path"], name="fs_path_idx"),
            models.Index(fields=["node_type"], name="fs_type_idx"),
            models.Index(fields=["created_at"], name="fs_created_idx"),
        ]

    def __str__(self) -> str:
        prefix = "DIR" if self.node_type == self.NodeType.DIR else "FILE"
        return f"{prefix} {self.path or self.name}"

    # -------------------------- Validation & Helpers --------------------------

    def clean(self):
        super().clean()

        # Name cannot contain path separators or null bytes
        if not self.name or "/" in self.name or "\x00" in self.name:
            raise ValidationError({"name": "Name must be non-empty and contain no '/' or null bytes."})

        # Parent must be a directory (if provided)
        if self.parent_id:
            if self.parent.filespace_id != self.filespace_id:
                raise ValidationError({"parent": "Parent must belong to the same filespace."})
            if self.parent.node_type != self.NodeType.DIR:
                raise ValidationError({"parent": "Parent must be a directory node."})

            # Prevent cycles
            cur = self.parent
            while cur is not None:
                if cur.pk == self.pk:
                    raise ValidationError({"parent": "Cannot set a node as a descendant of itself."})
                cur = cur.parent

        # File nodes shouldn't be deleted without timestamp, and vice versa; keep it light.
        if self.is_deleted and not self.deleted_at:
            self.deleted_at = timezone.now()

        # Content constraints
        if self.node_type == self.NodeType.DIR:
            self.content = None
            self.size_bytes = None

    def _compute_path(self) -> str:
        parts = [self.name]
        cur = self.parent
        while cur is not None:
            parts.append(cur.name)
            cur = cur.parent
        return "/" + "/".join(reversed(parts))

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        old_path = None
        old_is_deleted = None
        
        if not is_new and self.pk:
            try:
                old = AgentFsNode.objects.get(pk=self.pk)
                old_path = old.path
                old_is_deleted = old.is_deleted
            except AgentFsNode.DoesNotExist:
                old_path = None
                old_is_deleted = None

        # compute or refresh path cache before saving
        self.path = self._compute_path()

        # If a file, try to capture size if available
        if self.node_type == self.NodeType.FILE and self.content and hasattr(self.content, "size"):
            self.size_bytes = self.content.size

        self.full_clean()
        super().save(*args, **kwargs)

        # If path has changed due to rename or move, update descendants' path cache FIRST
        # This must happen before propagating deletion to ensure descendants are found correctly
        # Keep it simple and explicit; acceptable for pragmatic sizes.
        if old_path and old_path != self.path and self.node_type == self.NodeType.DIR:
            # Example:
            #   old_path = /a/b
            #   new_path = /x/y
            # Children paths start with old_path + '/'
            prefix = old_path.rstrip("/") + "/"
            new_prefix = self.path.rstrip("/") + "/"

            # Fast, safe bulk update: replace the leading prefix with the new prefix
            # using SQL substring/concat instead of Python-side per-row recompute.
            # Works across backends via Django functions.
            from django.db.models import Value
            from django.db.models.functions import Concat, Substr

            old_prefix_len = len(prefix)
            (AgentFsNode.objects
                .filter(filespace=self.filespace, path__startswith=prefix)
                .update(path=Concat(Value(new_prefix), Substr('path', old_prefix_len + 1))))

        # Handle subtree deletion: if this directory was just marked as deleted, 
        # propagate deletion to all descendants in the same transaction
        # This happens AFTER path updates to ensure descendants are found correctly
        if (self.node_type == self.NodeType.DIR and 
            self.is_deleted and 
            old_is_deleted is not None and 
            not old_is_deleted):
            self._propagate_deletion_to_descendants()

    # Convenience flags
    @property
    def is_dir(self) -> bool:
        return self.node_type == self.NodeType.DIR

    @property
    def is_file(self) -> bool:
        return self.node_type == self.NodeType.FILE

    def object_key_for(self, filename: str | None = None) -> str:
        """
        Compute the exact object-store key we will use for a new upload.
        Safe to call before saving, because UUIDs are generated client-side.
        """
        base = filename or self.name or "file"
        basename = os.path.basename(base)
        if not basename:  # Handle empty basename from paths like "///"
            basename = self.name or "file"
        safe = get_valid_filename(basename)
        return f"agent_fs/{self.filespace_id}/{self.id}/{safe}"

    @property
    def object_key(self) -> str | None:
        """
        The key of the *current* blob (if any). Falls back to the key we
        would use if we uploaded now using self.name.
        """
        if self.content and getattr(self.content, "name", None):
            return self.content.name
        return self.object_key_for()

    def _propagate_deletion_to_descendants(self):
        """
        Internal method to propagate soft deletion to all descendants.
        Called automatically when a directory is marked as deleted.
        """
        if self.node_type != self.NodeType.DIR:
            return
        
        # Find all descendants that are not already deleted
        descendants = AgentFsNode.objects.filter(
            filespace=self.filespace,
            path__startswith=self.path.rstrip("/") + "/",
            is_deleted=False
        )
        
        # Bulk update all descendants to mark them as deleted
        now = timezone.now()
        descendants.update(
            is_deleted=True,
            deleted_at=now
        )

    def trash_subtree(self):
        """
        Public helper method to soft-delete this node and all its descendants.
        
        This is a convenience method that can be used instead of setting
        is_deleted=True manually. It ensures consistent behavior for subtree deletion.
        
        Returns:
            int: Number of nodes that were deleted (including this node)
        """
        # Count descendants that will be deleted
        if self.node_type == self.NodeType.DIR:
            descendant_count = AgentFsNode.objects.filter(
                filespace=self.filespace,
                path__startswith=self.path.rstrip("/") + "/",
                is_deleted=False
            ).count()
        else:
            descendant_count = 0
        
        # Mark this node as deleted (will trigger automatic descendant deletion if it's a directory)
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])
        
        # Return total count of deleted nodes (this node + descendants)
        return 1 + descendant_count

    def restore_subtree(self):
        """
        Restore this node and all its descendants from soft deletion.
        
        Note: This will only restore nodes that were deleted. It will not
        restore nodes whose ancestors are still deleted (those would be
        inaccessible anyway).
        
        Returns:
            int: Number of nodes that were restored (including this node)
        """
        count = 0
        
        # Restore this node if it was deleted
        if self.is_deleted:
            self.is_deleted = False
            self.deleted_at = None
            self.save(update_fields=['is_deleted', 'deleted_at'])
            count += 1
        
        # If this is a directory, restore all descendants
        if self.node_type == self.NodeType.DIR:
            descendants = AgentFsNode.objects.filter(
                filespace=self.filespace,
                path__startswith=self.path.rstrip("/") + "/",
                is_deleted=True
            )
            
            descendant_count = descendants.update(
                is_deleted=False,
                deleted_at=None
            )
            count += descendant_count
        
        return count

    def get_descendants(self, include_deleted=False):
        """
        Get all descendants of this node.
        
        Args:
            include_deleted (bool): Whether to include soft-deleted nodes
            
        Returns:
            QuerySet: All descendant nodes
        """
        if self.node_type != self.NodeType.DIR:
            return AgentFsNode.objects.none()
        
        descendants = AgentFsNode.objects.filter(
            filespace=self.filespace,
            path__startswith=self.path.rstrip("/") + "/"
        )
        
        if not include_deleted:
            descendants = descendants.filter(is_deleted=False)
            
        return descendants


# Auto-provision a default filespace for new PersistentAgents
@receiver(post_save, sender=PersistentAgent)
def create_default_filespace_for_agent(sender, instance: PersistentAgent, created: bool, **kwargs):
    if not created:
        return
    try:
        fs = AgentFileSpace.objects.create(
            name=f"{instance.name} Files",
            owner_user=instance.user,
        )
        AgentFileSpaceAccess.objects.create(
            filespace=fs,
            agent=instance,
            role=AgentFileSpaceAccess.Role.OWNER,
            is_default=True,
        )
    except Exception as e:
        logger.error("Failed creating default filespace for agent %s: %s", instance.id, e)
        # Non-fatal; agent can operate without a default filespace.


@receiver(pre_delete, sender=PersistentAgent)
def cleanup_redis_budget_data(sender, instance: PersistentAgent, **kwargs):
    """Clean up Redis budget data when a PersistentAgent is deleted."""
    from config.redis_client import get_redis_client
    
    agent_id = str(instance.id)
    redis = get_redis_client()
    
    # Clean up all budget-related keys for this agent
    keys_to_delete = [
        f"pa:budget:{agent_id}",
        f"pa:budget:{agent_id}:steps",
        f"pa:budget:{agent_id}:branches",
        f"pa:budget:{agent_id}:active"
    ]
    
    try:
        if keys_to_delete:
            redis.delete(*keys_to_delete)
            logger.info("Cleaned up Redis budget data for deleted agent %s", agent_id)
    except Exception as e:
        logger.warning("Failed to clean up Redis budget data for agent %s: %s", agent_id, e)
        # Non-fatal; data will expire via TTL


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    plan = models.CharField(max_length=50, default="free")
    is_active = models.BooleanField(default=True)
    org_settings = models.JSONField(default=dict, blank=True)   # retention, redaction, SSO, etc.
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        # Show human-friendly label in admin selects/lists
        return f"{self.name} ({self.id})"

class OrganizationMembership(models.Model):
    class OrgRole(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        BILLING = "billing_admin", "Billing"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"
    class OrgStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        REMOVED = "removed", "Removed"

    org = models.ForeignKey(Organization, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=OrgRole.choices)
    status = models.CharField(max_length=20, choices=OrgStatus.choices, default=OrgStatus.ACTIVE)  # active|removed

    class Meta:
        unique_together = ("org", "user")

class OrganizationInvite(models.Model):
    org = models.ForeignKey(Organization, on_delete=models.CASCADE)
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=OrganizationMembership.OrgRole.choices)
    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    sent_at = models.DateTimeField(auto_now_add=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
