import hashlib, secrets, uuid, os, string, re, datetime, json
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from typing import Optional, Tuple

import ulid
from django.apps import apps
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.validators import RegexValidator
from django.db import IntegrityError, connection, models, transaction
from django.db.models import Count, Q, Sum, UniqueConstraint
from django.db.models.functions import Lower
from django.db.models.functions.datetime import TruncMonth
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.utils.text import get_valid_filename
from django.db.utils import OperationalError, ProgrammingError

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, post_delete, pre_delete, pre_save

from django.dispatch import receiver

from agents.services import AgentService
from config.plans import PLAN_CONFIG
from config.settings import INITIAL_TASK_CREDIT_EXPIRATION_DAYS
from constants.grant_types import GrantTypeChoices
from constants.plans import (
    PlanNames,
    PlanNamesChoices,
    UserPlanNamesChoices,
    OrganizationPlanNamesChoices,
)
from api.services.prompt_settings import (
    DEFAULT_MAX_MESSAGE_HISTORY_LIMIT,
    DEFAULT_MAX_PROMPT_TOKEN_BUDGET,
    DEFAULT_MAX_TOOL_CALL_HISTORY_LIMIT,
    DEFAULT_ULTRA_MAX_MESSAGE_HISTORY_LIMIT,
    DEFAULT_ULTRA_MAX_PROMPT_TOKEN_BUDGET,
    DEFAULT_ULTRA_MAX_TOOL_CALL_HISTORY_LIMIT,
    DEFAULT_ULTRA_MESSAGE_HISTORY_LIMIT,
    DEFAULT_ULTRA_PROMPT_TOKEN_BUDGET,
    DEFAULT_ULTRA_TOOL_CALL_HISTORY_LIMIT,
    DEFAULT_PREMIUM_MESSAGE_HISTORY_LIMIT,
    DEFAULT_PREMIUM_PROMPT_TOKEN_BUDGET,
    DEFAULT_PREMIUM_TOOL_CALL_HISTORY_LIMIT,
    DEFAULT_STANDARD_MESSAGE_HISTORY_LIMIT,
    DEFAULT_STANDARD_PROMPT_TOKEN_BUDGET,
    DEFAULT_STANDARD_TOOL_CALL_HISTORY_LIMIT,
    DEFAULT_STANDARD_ENABLED_TOOL_LIMIT,
    DEFAULT_PREMIUM_ENABLED_TOOL_LIMIT,
    DEFAULT_MAX_ENABLED_TOOL_LIMIT,
    DEFAULT_ULTRA_ENABLED_TOOL_LIMIT,
    DEFAULT_ULTRA_MAX_ENABLED_TOOL_LIMIT,
    DEFAULT_UNIFIED_HISTORY_LIMIT,
    DEFAULT_UNIFIED_HISTORY_HYSTERESIS,
)
from api.services.browser_settings import (
    DEFAULT_MAX_ACTIVE_BROWSER_TASKS,
    DEFAULT_MAX_BROWSER_STEPS,
    DEFAULT_MAX_BROWSER_TASKS,
    DEFAULT_VISION_DETAIL_LEVEL,
)
from api.services.mcp_tool_cache import invalidate_mcp_tool_cache
from api.services.tool_settings import (
    DEFAULT_MIN_CRON_SCHEDULE_MINUTES,
    DEFAULT_SEARCH_WEB_RESULT_COUNT,
    DEFAULT_SEARCH_ENGINE_BATCH_QUERY_LIMIT,
    DEFAULT_BRIGHTDATA_AMAZON_PRODUCT_SEARCH_LIMIT,
    DEFAULT_DUPLICATE_SIMILARITY_THRESHOLD,
)
from constants.regex import E164_PHONE_REGEX
from observability import traced
from email.utils import parseaddr

from tasks.services import TaskCreditService

from util.subscription_helper import (
    get_active_subscription, )
from util.tool_costs import get_default_task_credit_cost
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

DEFAULT_INTELLIGENCE_TIER_KEY = "standard"


class IntelligenceTier(models.Model):
    """Configurable intelligence tier for LLM routing and credit multipliers."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=32, unique=True)
    display_name = models.CharField(max_length=64)
    rank = models.PositiveSmallIntegerField(
        unique=True,
        help_text="Higher rank means higher intelligence tier.",
    )
    credit_multiplier = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Multiplier applied to credit consumption for this intelligence tier.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rank", "key"]

    def __str__(self):
        return f"{self.display_name} ({self.key})"

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        _invalidate_tier_multiplier_cache()
        return result

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        _invalidate_tier_multiplier_cache()
        return result


def _get_default_intelligence_tier_id() -> uuid.UUID | None:
    tier = IntelligenceTier.objects.filter(key=DEFAULT_INTELLIGENCE_TIER_KEY).only("id").first()
    return tier.id if tier else None


def _apply_tier_multiplier(agent, amount):
    from api.agent.core import llm_config

    return llm_config.apply_tier_credit_multiplier(agent, amount)


def _invalidate_tier_multiplier_cache() -> None:
    from api.agent.core import llm_config

    llm_config.invalidate_llm_tier_multiplier_cache()


class AgentColor(models.Model):
    """Palette entry for agent accent colors."""

    DEFAULT_HEX = "#0074d4"

    name = models.CharField(max_length=64, unique=True)
    hex_value = models.CharField(max_length=7, unique=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.name} ({self.hex_value})"

    @classmethod
    def get_active_palette(cls) -> list["AgentColor"]:
        """Return the ordered, active palette."""
        cls._seed_palette_if_needed()
        return list(cls.objects.filter(is_active=True).order_by("sort_order", "id"))

    @classmethod
    def get_default_hex(cls) -> str:
        """Return the default hex value, even if the palette isn't yet seeded."""
        palette = cls.get_active_palette()
        if palette:
            return palette[0].hex_value
        return cls.DEFAULT_HEX

    @classmethod
    def pick_for_owner(cls, *, user, organization=None) -> "AgentColor | None":
        """Prefer a unique color for the owner; otherwise reuse the least-used palette color."""
        palette = cls.get_active_palette()
        if not palette:
            return None

        qs = PersistentAgent.objects.filter(agent_color__isnull=False)
        organization_id = None
        if organization is not None:
            organization_id = getattr(organization, "id", None)
            if organization_id is None:
                if isinstance(organization, uuid.UUID):
                    organization_id = str(organization)
                elif isinstance(organization, (int, str)):
                    organization_id = organization

        if organization_id:
            qs = qs.filter(organization_id=organization_id)
        else:
            qs = qs.filter(organization__isnull=True, user=user)

        usage_counts = {
            row["agent_color_id"]: row["count"]
            for row in qs.values("agent_color_id").annotate(count=Count("id"))
        }

        for color in palette:
            if usage_counts.get(color.id, 0) == 0:
                return color

        least_used_color = min(
            palette,
            key=lambda color: (usage_counts.get(color.id, 0), color.sort_order, color.id),
        )
        return least_used_color

    @classmethod
    def _seed_palette_if_needed(cls) -> None:
        """Populate the palette when migrations are disabled (e.g., unit tests)."""
        try:
            if cls.objects.filter(is_active=True).exists():
                return
        except (ProgrammingError, OperationalError):
            # Table does not exist yet (e.g., during migrations); skip seeding.
            return

        with transaction.atomic():
            cls.objects.update_or_create(
                name=f"color_0",
                defaults={
                    "hex_value": cls.DEFAULT_HEX,
                    "sort_order": 0,
                    "is_active": True,
                },
            )


# ---------------------------------------------------------------------------
#  Web chat addressing helpers
# ---------------------------------------------------------------------------

WEB_USER_ADDRESS_RE = re.compile(r"^web://user/(?P<user_id>-?\d+)/agent/(?P<agent_id>[0-9a-fA-F-]+)$")
WEB_AGENT_ADDRESS_RE = re.compile(r"^web://agent/(?P<agent_id>[0-9a-fA-F-]+)$")


def build_web_user_address(user_id: int, agent_id: uuid.UUID | str) -> str:
    """Return canonical address for a user participating in web chat with an agent."""
    return f"web://user/{user_id}/agent/{agent_id}"


def build_web_agent_address(agent_id: uuid.UUID | str) -> str:
    """Return canonical address for the agent's web chat identity."""
    return f"web://agent/{agent_id}"


def parse_web_user_address(address: str) -> Tuple[Optional[int], Optional[str]]:
    """Parse a user web-chat address and return (user_id, agent_id) if valid."""
    match = WEB_USER_ADDRESS_RE.match((address or "").strip())
    if not match:
        return None, None
    try:
        user_id = int(match.group("user_id"))
    except (TypeError, ValueError):
        return None, None
    return user_id, match.group("agent_id")

def _hash(raw: str) -> str:
    """Return SHA256 hexdigest for given raw string."""
    return hashlib.sha256(raw.encode()).hexdigest()

def get_default_execution_environment() -> str:
    """Return the default execution environment from GOBII_RELEASE_ENV."""
    return os.getenv("GOBII_RELEASE_ENV", "local")


class PersistentAgentQuerySet(models.QuerySet):
    """Custom queryset helpers for PersistentAgent."""

    def non_eval(self):
        """Exclude agents created for eval runs."""
        return self.exclude(execution_environment="eval")


class CommsChannel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    SLACK = "slack", "Slack"
    DISCORD = "discord", "Discord"
    WEB = "web", "Web Chat"
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
    MAX_API_KEYS_PER_ORG = 50

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        "Organization",
        on_delete=models.CASCADE,
        related_name="api_keys",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_api_keys",
        null=True,
        blank=True,
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
            UniqueConstraint(
                fields=['user', 'name'],
                name='unique_api_key_user_name',
                condition=models.Q(organization__isnull=True, user__isnull=False),
            ),
            UniqueConstraint(
                fields=['organization', 'name'],
                name='unique_api_key_org_name',
                condition=models.Q(organization__isnull=False),
            ),
            models.CheckConstraint(
                check=(
                    (models.Q(user__isnull=False, organization__isnull=True))
                    | (models.Q(user__isnull=True, organization__isnull=False))
                ),
                name="api_key_exactly_one_owner",
            ),
        ]

    @staticmethod
    def generate() -> tuple[str, str]:
        raw = secrets.token_urlsafe(32)
        return raw, _hash(raw)

    @classmethod
    def create_for_user(cls, user, name="default", *, created_by=None):
        raw, hashed = cls.generate()
        prefix = raw[:8]
        instance = cls.objects.create(
            user=user,
            name=name,
            prefix=prefix,
            hashed_key=hashed,
            raw_key=raw,
            created_by=created_by or user,
        )
        return raw, instance

    @classmethod
    def create_for_org(cls, organization, *, created_by, name="default"):
        if created_by is None:
            raise ValueError("created_by is required for organization API keys")

        raw, hashed = cls.generate()
        prefix = raw[:8]
        instance = cls.objects.create(
            organization=organization,
            created_by=created_by,
            name=name,
            prefix=prefix,
            hashed_key=hashed,
            raw_key=raw,
        )
        return raw, instance

    def clean(self):
        super().clean()
        owner_user = getattr(self, 'user', None)
        owner_org = getattr(self, 'organization', None)
        owner_user_id = getattr(self, 'user_id', None) or (owner_user.id if owner_user else None)
        owner_org_id = getattr(self, 'organization_id', None) or (owner_org.id if owner_org else None)

        if self._state.adding:

            if bool(owner_user_id) == bool(owner_org_id):
                raise ValidationError("API keys must belong to exactly one owner (user or organization).")

            if owner_user_id:
                current_key_count = ApiKey.objects.filter(user_id=owner_user_id).count()
                if current_key_count >= self.MAX_API_KEYS_PER_USER:
                    raise ValidationError(
                        f"You have reached the maximum limit of {self.MAX_API_KEYS_PER_USER} API keys."
                    )

            if owner_org_id:
                current_key_count = ApiKey.objects.filter(organization_id=owner_org_id).count()
                if current_key_count >= self.MAX_API_KEYS_PER_ORG:
                    raise ValidationError(
                        f"This organization has reached the maximum limit of {self.MAX_API_KEYS_PER_ORG} API keys."
                    )

        if owner_user_id:
            existing = ApiKey.objects.filter(
                user_id=owner_user_id,
                name__iexact=self.name,
            )
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError("You already have an API key with that name.")

        if owner_org_id:
            existing = ApiKey.objects.filter(
                organization_id=owner_org_id,
                name__iexact=self.name,
            )
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError("This organization already has an API key with that name.")

    def save(self, *args, **kwargs):
        if self.user_id and self.created_by_id is None:
            self.created_by_id = self.user_id
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
    # Optional per-user override for max contacts per agent; when null or <= 0, plan default applies
    max_agent_contacts = models.PositiveIntegerField(null=True, blank=True, default=None,
                                                    help_text="If set (>0), overrides plan max contacts per agent for this user")

    def __str__(self):
        return f"Quota for {self.user.email}"


class UserFlags(models.Model):
    """Optional per-user feature flags/switches."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="flags",
    )
    is_vip = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User flags"
        verbose_name_plural = "User flags"

    @classmethod
    def get_for_user(cls, user):
        if not user or not getattr(user, "pk", None):
            return None

        if "flags" in getattr(user, "__dict__", {}):
            return user.__dict__["flags"]

        prefetched = getattr(user, "_prefetched_objects_cache", None)
        if prefetched and "flags" in prefetched:
            return prefetched.get("flags")

        return cls.objects.filter(user=user).first()

    @classmethod
    def ensure_for_user(cls, user):
        if not user or not getattr(user, "pk", None):
            raise ValueError("User must be saved before ensuring flags.")

        return cls.objects.get_or_create(user=user)[0]


def _user_is_vip(self):
    """Safe VIP accessor that tolerates missing flags rows."""
    if not getattr(self, "pk", None):
        return False

    flags = None
    if "flags" in getattr(self, "__dict__", {}):
        flags = self.__dict__["flags"]
    elif getattr(self, "_prefetched_objects_cache", None) and "flags" in self._prefetched_objects_cache:
        flags = self._prefetched_objects_cache.get("flags")

    if flags is None:
        flags = UserFlags.objects.filter(user=self).only("is_vip").first()

    return bool(flags and getattr(flags, "is_vip", False))


UserModel = get_user_model()
if not hasattr(UserModel, "is_vip"):
    UserModel.add_to_class("is_vip", property(_user_is_vip))


class UserReferral(models.Model):
    """
    Stores a user's referral code for sharing with others.
    Created lazily when user first requests their referral link.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referral",
    )
    referral_code = models.CharField(
        max_length=12,
        unique=True,
        db_index=True,
        help_text="Unique code this user shares to refer others"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User Referral"
        verbose_name_plural = "User Referrals"

    def __str__(self):
        return f"{self.referral_code} ({self.user_id})"

    @classmethod
    def generate_code(cls, length=8, max_attempts=100):
        """Generate a random alphanumeric referral code."""
        alphabet = string.ascii_uppercase + string.digits
        # Remove ambiguous characters (0, O, I, 1, L)
        alphabet = alphabet.replace('0', '').replace('O', '').replace('I', '').replace('1', '').replace('L', '')
        for _ in range(max_attempts):
            code = ''.join(secrets.choice(alphabet) for _ in range(length))
            if not cls.objects.filter(referral_code=code).exists():
                return code
        raise RuntimeError("Failed to generate unique referral code after max attempts")

    @classmethod
    def get_or_create_for_user(cls, user):
        """Get existing referral code or create one for the user."""
        try:
            return cls.objects.get(user=user)
        except cls.DoesNotExist:
            try:
                code = cls.generate_code()
                return cls.objects.create(user=user, referral_code=code)
            except IntegrityError:
                # The object was created by another process after the initial get failed.
                # We can now safely get it.
                return cls.objects.get(user=user)

    @classmethod
    def get_user_by_code(cls, code):
        """Look up the user who owns a given referral code. Returns None if not found."""
        try:
            return cls.objects.select_related('user').get(referral_code=code).user
        except cls.DoesNotExist:
            return None


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
        help_text="Type of grant for these credits (e.g., PLAN, COMPENSATION, PROMO, TASK_PACK)"
    )

    voided = models.BooleanField(
        default=False,
        help_text="Whether this credit block has been voided and should not be used"
    )

    comments = models.TextField(
        blank=True,
        default='',
        help_text="Optional notes about this credit grant (e.g., reason for compensation, promo details)"
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



class TaskCreditConfig(models.Model):
    """Singleton configuration for default task credit consumption."""

    singleton_id = models.PositiveSmallIntegerField(
        primary_key=True,
        default=1,
        editable=False,
    )
    default_task_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Default credit cost applied when no tool-specific override exists.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Task credit configuration"
        verbose_name_plural = "Task credit configuration"

    def save(self, *args, **kwargs):  # pragma: no cover - exercised via util tests
        self.singleton_id = 1
        result = super().save(*args, **kwargs)
        from util.tool_costs import clear_tool_credit_cost_cache

        clear_tool_credit_cost_cache()
        return result

    def delete(self, using=None, keep_parents=False):  # pragma: no cover - deletion discouraged
        raise ValidationError("TaskCreditConfig cannot be deleted.")

    def __str__(self):
        return "Task credit configuration"


class BurnRateSnapshot(models.Model):
    """Cached burn-rate metrics for owners and agents."""

    class ScopeType(models.TextChoices):
        USER = "user", "User"
        ORGANIZATION = "organization", "Organization"
        AGENT = "agent", "Agent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope_type = models.CharField(max_length=32, choices=ScopeType.choices)
    scope_id = models.CharField(max_length=64)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="burn_rate_snapshots",
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        "Organization",
        on_delete=models.CASCADE,
        related_name="burn_rate_snapshots",
        null=True,
        blank=True,
    )
    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="burn_rate_snapshots",
        null=True,
        blank=True,
    )
    window_minutes = models.PositiveIntegerField()
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    window_total = models.DecimalField(max_digits=20, decimal_places=6)
    burn_rate_per_hour = models.DecimalField(max_digits=20, decimal_places=6)
    burn_rate_per_day = models.DecimalField(max_digits=20, decimal_places=6)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope_type", "scope_id", "window_minutes"],
                name="burn_rate_snapshot_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["scope_type", "scope_id", "window_minutes"],
                name="burn_rate_snapshot_scope_idx",
            ),
        ]
        ordering = ["-computed_at"]


class ReferralIncentiveConfig(models.Model):
    """Singleton configuration for referral incentive grants."""

    singleton_id = models.PositiveSmallIntegerField(
        primary_key=True,
        default=1,
        editable=False,
    )
    referrer_direct_credits = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
        default=Decimal("100"),
        help_text="Credits granted to the referrer for direct account referrals.",
    )
    referred_direct_credits = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
        default=Decimal("100"),
        help_text="Credits granted to the referred user for direct account referrals.",
    )
    referrer_template_credits = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
        default=Decimal("100"),
        help_text="Credits granted to the referrer for shared template referrals.",
    )
    referred_template_credits = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
        default=Decimal("100"),
        help_text="Credits granted to the referred user for shared template referrals.",
    )
    direct_referral_cap = models.PositiveIntegerField(
        default=25,
        help_text="Lifetime cap on the number of direct referral grants per referrer.",
    )
    template_referral_cap = models.PositiveIntegerField(
        default=25,
        help_text="Lifetime cap on the number of template referral grants per referrer.",
    )
    expiration_days = models.PositiveIntegerField(
        default=30,
        help_text="Number of days before referral credits expire.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Referral incentive configuration"
        verbose_name_plural = "Referral incentive configuration"

    @classmethod
    def get_solo(cls):
        return cls.objects.get_or_create(singleton_id=1)[0]

    def save(self, *args, **kwargs):  # pragma: no cover - simple singleton guard
        self.singleton_id = 1
        return super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):  # pragma: no cover - deletion discouraged
        raise ValidationError("ReferralIncentiveConfig cannot be deleted.")

    def __str__(self):
        return "Referral incentive configuration"


class Plan(models.Model):
    """Stable plan identity (e.g., free, startup, scale)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.CharField(
        max_length=64,
        unique=True,
        help_text="Stable plan identifier used across versions (e.g., free, startup).",
    )
    is_org = models.BooleanField(
        default=False,
        help_text="Whether this plan is for organizations.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this plan is available for use.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        verbose_name = "Plan"
        verbose_name_plural = "Plans"

    def __str__(self) -> str:
        return self.slug


class PlanVersion(models.Model):
    """Versioned entitlements + marketing copy for a plan."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version_code = models.CharField(
        max_length=64,
        help_text="Version code unique per plan (e.g., v1, 2024-10).",
    )
    legacy_plan_code = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        help_text="Legacy plan identifier (e.g., pln_l_m_v1).",
    )
    is_active_for_new_subs = models.BooleanField(
        default=False,
        help_text="Whether this version is selectable for new subscriptions.",
    )
    display_name = models.CharField(max_length=128)
    tagline = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    marketing_features = models.JSONField(default=list, blank=True)
    effective_start_at = models.DateTimeField(null=True, blank=True)
    effective_end_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan__slug", "-created_at"]
        constraints = [
            UniqueConstraint(
                fields=["plan", "version_code"],
                name="unique_plan_version_code",
            ),
            UniqueConstraint(
                fields=["plan"],
                condition=Q(is_active_for_new_subs=True),
                name="unique_active_plan_version",
            ),
        ]
        verbose_name = "Plan version"
        verbose_name_plural = "Plan versions"

    def __str__(self) -> str:
        label = f"{self.plan.slug}:{self.version_code}"
        if self.legacy_plan_code:
            return f"{label} ({self.legacy_plan_code})"
        return label


class PlanPriceKindChoices(models.TextChoices):
    BASE = "base", "Base"
    SEAT = "seat", "Seat"
    OVERAGE = "overage", "Overage"
    TASK_PACK = "task_pack", "Task pack"
    CONTACT_PACK = "contact_pack", "Contact pack"
    BROWSER_TASK_LIMIT = "browser_task_limit", "Browser task limit"
    ADVANCED_CAPTCHA_RESOLUTION = "advanced_captcha_resolution", "Advanced captcha resolution"
    DEDICATED_IP = "dedicated_ip", "Dedicated IP"


class PlanBillingIntervalChoices(models.TextChoices):
    MONTH = "month", "Monthly"
    YEAR = "year", "Yearly"


class PlanVersionPrice(models.Model):
    """Stripe price mapping for a plan version."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan_version = models.ForeignKey(
        PlanVersion,
        on_delete=models.CASCADE,
        related_name="prices",
    )
    kind = models.CharField(max_length=32, choices=PlanPriceKindChoices.choices)
    billing_interval = models.CharField(
        max_length=8,
        choices=PlanBillingIntervalChoices.choices,
        null=True,
        blank=True,
        help_text="Billing interval for recurring prices; null for metered/add-ons.",
    )
    price_id = models.CharField(max_length=255)
    product_id = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan_version", "kind", "price_id"]
        constraints = [
            UniqueConstraint(
                fields=["plan_version", "price_id"],
                name="unique_plan_version_price_id",
            ),
        ]
        indexes = [
            models.Index(fields=["price_id"], name="planverprice_price_idx"),
            models.Index(fields=["product_id"], name="planverprice_product_idx"),
        ]
        verbose_name = "Plan version price"
        verbose_name_plural = "Plan version prices"

    def __str__(self) -> str:
        return f"{self.plan_version_id}:{self.kind}:{self.price_id}"


class EntitlementValueTypeChoices(models.TextChoices):
    INT = "int", "Integer"
    DECIMAL = "decimal", "Decimal"
    BOOL = "bool", "Boolean"
    TEXT = "text", "Text"
    JSON = "json", "JSON"


class EntitlementDefinition(models.Model):
    """Definition of a plan entitlement key/value."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=128, unique=True)
    display_name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    value_type = models.CharField(max_length=16, choices=EntitlementValueTypeChoices.choices)
    unit = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        verbose_name = "Entitlement definition"
        verbose_name_plural = "Entitlement definitions"

    def __str__(self) -> str:
        return self.key


class PlanVersionEntitlement(models.Model):
    """Entitlement values scoped to a plan version."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan_version = models.ForeignKey(
        PlanVersion,
        on_delete=models.CASCADE,
        related_name="entitlements",
    )
    entitlement = models.ForeignKey(
        EntitlementDefinition,
        on_delete=models.CASCADE,
        related_name="plan_values",
    )
    value_int = models.IntegerField(null=True, blank=True)
    value_decimal = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    value_bool = models.BooleanField(null=True, blank=True)
    value_text = models.TextField(null=True, blank=True)
    value_json = models.JSONField(null=True, blank=True)
    currency = models.CharField(max_length=16, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan_version", "entitlement__key"]
        constraints = [
            UniqueConstraint(
                fields=["plan_version", "entitlement"],
                name="unique_plan_version_entitlement",
            ),
        ]
        verbose_name = "Plan version entitlement"
        verbose_name_plural = "Plan version entitlements"

    def __str__(self) -> str:
        return f"{self.plan_version_id}:{self.entitlement.key}"


class DailyCreditConfig(models.Model):
    """Per-plan configuration controlling soft target UI + pacing."""

    id = models.BigAutoField(primary_key=True)
    plan_version = models.ForeignKey(
        PlanVersion,
        on_delete=models.CASCADE,
        related_name="daily_credit_configs",
        null=True,
        blank=True,
        help_text="Plan version the daily credit pacing settings apply to.",
    )
    plan_name = models.CharField(
        max_length=32,
        choices=PlanNamesChoices.choices,
        null=True,
        blank=True,
        help_text="Legacy plan identifier the daily credit pacing settings apply to.",
    )
    slider_min = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Lowest selectable soft target value.",
    )
    slider_max = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("50"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Highest selectable soft target value.",
    )
    slider_step = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Increment applied to the soft target slider/input.",
    )
    burn_rate_threshold_per_hour = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("3"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Preferred maximum credits consumed per hour before agents are asked to slow down.",
    )
    burn_rate_window_minutes = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
        help_text="Window (in minutes) used to compute the rolling burn rate.",
    )
    hard_limit_multiplier = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("2"),
        validators=[MinValueValidator(Decimal("1"))],
        help_text="Multiplier applied to the soft target to derive the enforced hard limit.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan_name"]
        verbose_name = "Daily credit pacing configuration"
        verbose_name_plural = "Daily credit pacing configuration"
        constraints = [
            UniqueConstraint(
                fields=["plan_version"],
                condition=Q(plan_version__isnull=False),
                name="unique_daily_credit_plan_version",
            ),
            UniqueConstraint(
                fields=["plan_name"],
                condition=Q(plan_name__isnull=False),
                name="unique_daily_credit_plan_name",
            ),
        ]

    def clean(self):
        super().clean()
        if self.slider_max < self.slider_min:
            raise ValidationError({"slider_max": "Maximum must be greater than or equal to the minimum value."})
        integer_fields = {
            "slider_min": self.slider_min,
            "slider_max": self.slider_max,
            "slider_step": self.slider_step,
        }
        for field_name, value in integer_fields.items():
            if value is None:
                continue
            if value != value.to_integral_value(rounding=ROUND_DOWN):
                raise ValidationError({field_name: "Value must be a whole number."})

    def save(self, *args, **kwargs):
        if self.plan_name:
            self.plan_name = self.plan_name.lower()
        result = super().save(*args, **kwargs)
        from api.services.daily_credit_settings import invalidate_daily_credit_settings_cache

        invalidate_daily_credit_settings_cache()
        return result

    def delete(self, using=None, keep_parents=False):  # pragma: no cover - deletion discouraged
        raise ValidationError("DailyCreditConfig cannot be deleted.")

    def __str__(self):
        label = self.plan_name or str(self.plan_version_id or "unknown")
        return f"{label} daily credit pacing configuration"


class VisionDetailLevelChoices(models.TextChoices):
    AUTO = "auto", "Auto"
    LOW = "low", "Low"
    HIGH = "high", "High"


class BrowserConfig(models.Model):
    """Per-plan browser agent configuration."""

    id = models.BigAutoField(primary_key=True)
    plan_version = models.ForeignKey(
        PlanVersion,
        on_delete=models.CASCADE,
        related_name="browser_configs",
        null=True,
        blank=True,
        help_text="Plan version the browser limits apply to.",
    )
    plan_name = models.CharField(
        max_length=32,
        choices=PlanNamesChoices.choices,
        null=True,
        blank=True,
        help_text="Legacy plan identifier the browser limits apply to.",
    )
    max_browser_steps = models.PositiveIntegerField(
        default=DEFAULT_MAX_BROWSER_STEPS,
        help_text="Maximum steps per browser task; set to 0 to use the system default.",
    )
    max_browser_tasks = models.PositiveIntegerField(
        default=DEFAULT_MAX_BROWSER_TASKS,
        help_text="Maximum browser tasks that can start each day; set to 0 for unlimited.",
    )
    max_active_browser_tasks = models.PositiveIntegerField(
        default=DEFAULT_MAX_ACTIVE_BROWSER_TASKS,
        help_text="Maximum concurrently active browser tasks; set to 0 for unlimited.",
    )
    vision_detail_level = models.CharField(
        max_length=8,
        choices=VisionDetailLevelChoices.choices,
        default=DEFAULT_VISION_DETAIL_LEVEL,
        help_text="Vision detail level to pass to browser_use when vision support is enabled.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan_name"]
        verbose_name = "Browser configuration"
        verbose_name_plural = "Browser configuration"
        constraints = [
            UniqueConstraint(
                fields=["plan_version"],
                condition=Q(plan_version__isnull=False),
                name="unique_browser_config_plan_version",
            ),
            UniqueConstraint(
                fields=["plan_name"],
                condition=Q(plan_name__isnull=False),
                name="unique_browser_config_plan_name",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.plan_name:
            self.plan_name = self.plan_name.lower()
        self.vision_detail_level = (self.vision_detail_level or DEFAULT_VISION_DETAIL_LEVEL).lower()
        result = super().save(*args, **kwargs)
        from api.services.browser_settings import invalidate_browser_settings_cache

        invalidate_browser_settings_cache()
        return result

    def delete(self, using=None, keep_parents=False):
        raise ValidationError("BrowserConfig cannot be deleted.")

    def __str__(self):
        label = self.plan_name or str(self.plan_version_id or "unknown")
        return f"{label} browser configuration"


class ToolConfig(models.Model):
    """Per-plan tool configuration."""

    id = models.BigAutoField(primary_key=True)
    plan_version = models.ForeignKey(
        PlanVersion,
        on_delete=models.CASCADE,
        related_name="tool_configs",
        null=True,
        blank=True,
        help_text="Plan version the tool configuration applies to.",
    )
    plan_name = models.CharField(
        max_length=32,
        choices=PlanNamesChoices.choices,
        null=True,
        blank=True,
        help_text="Legacy plan identifier the tool configuration applies to.",
    )
    min_cron_schedule_minutes = models.PositiveIntegerField(
        default=DEFAULT_MIN_CRON_SCHEDULE_MINUTES,
        help_text="Minimum allowed cron/interval frequency in minutes; set to 0 to disable enforcement.",
    )
    search_web_result_count = models.PositiveIntegerField(
        default=DEFAULT_SEARCH_WEB_RESULT_COUNT,
        help_text="Preferred number of results to return from search_web (Exa).",
    )
    search_engine_batch_query_limit = models.PositiveIntegerField(
        default=DEFAULT_SEARCH_ENGINE_BATCH_QUERY_LIMIT,
        help_text="Maximum number of queries allowed in a single search_engine_batch call.",
    )
    brightdata_amazon_product_search_limit = models.PositiveIntegerField(
        default=DEFAULT_BRIGHTDATA_AMAZON_PRODUCT_SEARCH_LIMIT,
        help_text="Maximum number of results to keep from Bright Data amazon product search.",
    )
    duplicate_similarity_threshold = models.FloatField(
        default=DEFAULT_DUPLICATE_SIMILARITY_THRESHOLD,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Similarity ratio required before blocking a potential duplicate outbound message.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan_name"]
        verbose_name = "Tool configuration"
        verbose_name_plural = "Tool configuration"
        constraints = [
            UniqueConstraint(
                fields=["plan_version"],
                condition=Q(plan_version__isnull=False),
                name="unique_tool_config_plan_version",
            ),
            UniqueConstraint(
                fields=["plan_name"],
                condition=Q(plan_name__isnull=False),
                name="unique_tool_config_plan_name",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.plan_name:
            self.plan_name = self.plan_name.lower()
        result = super().save(*args, **kwargs)
        from api.services.tool_settings import invalidate_tool_settings_cache

        invalidate_tool_settings_cache()
        return result

    def delete(self, using=None, keep_parents=False):
        raise ValidationError("ToolConfig cannot be deleted.")

    def __str__(self):
        label = self.plan_name or str(self.plan_version_id or "unknown")
        return f"{label} tool configuration"


class ToolRateLimit(models.Model):
    """Per-plan hourly rate limits for specific tools."""

    plan = models.ForeignKey(
        "ToolConfig",
        on_delete=models.CASCADE,
        related_name="rate_limits",
        help_text="Tool configuration the rate limit applies to.",
    )
    tool_name = models.CharField(
        max_length=128,
        help_text="Tool eligible for rate limiting. Use the tool name exactly as invoked by the agent.",
    )
    max_calls_per_hour = models.PositiveIntegerField(
        default=0,
        help_text="Maximum calls per agent in a sliding hour; set to 0 to disable enforcement.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan", "tool_name"]
        constraints = [
            UniqueConstraint(fields=["plan", "tool_name"], name="unique_tool_rate_limit_per_plan_tool"),
        ]
        verbose_name = "Tool rate limit"
        verbose_name_plural = "Tool rate limits"

    def save(self, *args, **kwargs):  # pragma: no cover - exercised via services
        self.tool_name = (self.tool_name or "").strip().lower()
        result = super().save(*args, **kwargs)
        from api.services.tool_settings import invalidate_tool_settings_cache

        invalidate_tool_settings_cache()
        return result

    def delete(self, *args, **kwargs):  # pragma: no cover - exercised via services
        result = super().delete(*args, **kwargs)
        from api.services.tool_settings import invalidate_tool_settings_cache

        invalidate_tool_settings_cache()
        return result

    def __str__(self):
        label = self.plan.plan_name or str(self.plan.plan_version_id or self.plan_id)
        return f"{label}:{self.tool_name} ({self.max_calls_per_hour}/hr)"


class PromptConfig(models.Model):
    """Singleton configuration controlling prompt and history limits."""

    singleton_id = models.PositiveSmallIntegerField(
        primary_key=True,
        default=1,
        editable=False,
    )
    standard_prompt_token_budget = models.PositiveIntegerField(
        default=DEFAULT_STANDARD_PROMPT_TOKEN_BUDGET,
        validators=[MinValueValidator(1)],
        help_text="Token budget applied when rendering prompts for standard tier agents.",
    )
    premium_prompt_token_budget = models.PositiveIntegerField(
        default=DEFAULT_PREMIUM_PROMPT_TOKEN_BUDGET,
        validators=[MinValueValidator(1)],
        help_text="Token budget applied when rendering prompts for premium tier agents.",
    )
    max_prompt_token_budget = models.PositiveIntegerField(
        default=DEFAULT_MAX_PROMPT_TOKEN_BUDGET,
        validators=[MinValueValidator(1)],
        help_text="Token budget applied when rendering prompts for max tier agents.",
    )
    ultra_prompt_token_budget = models.PositiveIntegerField(
        default=DEFAULT_ULTRA_PROMPT_TOKEN_BUDGET,
        validators=[MinValueValidator(1)],
        help_text="Token budget applied when rendering prompts for ultra tier agents.",
    )
    ultra_max_prompt_token_budget = models.PositiveIntegerField(
        default=DEFAULT_ULTRA_MAX_PROMPT_TOKEN_BUDGET,
        validators=[MinValueValidator(1)],
        help_text="Token budget applied when rendering prompts for ultra max tier agents.",
    )
    standard_message_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_STANDARD_MESSAGE_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent messages included for standard tier agents.",
    )
    premium_message_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_PREMIUM_MESSAGE_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent messages included for premium tier agents.",
    )
    max_message_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_MAX_MESSAGE_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent messages included for max tier agents.",
    )
    ultra_message_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_ULTRA_MESSAGE_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent messages included for ultra tier agents.",
    )
    ultra_max_message_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_ULTRA_MAX_MESSAGE_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent messages included for ultra max tier agents.",
    )
    standard_tool_call_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_STANDARD_TOOL_CALL_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent tool calls included for standard tier agents.",
    )
    premium_tool_call_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_PREMIUM_TOOL_CALL_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent tool calls included for premium tier agents.",
    )
    max_tool_call_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_MAX_TOOL_CALL_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent tool calls included for max tier agents.",
    )
    ultra_tool_call_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_ULTRA_TOOL_CALL_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent tool calls included for ultra tier agents.",
    )
    ultra_max_tool_call_history_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_ULTRA_MAX_TOOL_CALL_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of recent tool calls included for ultra max tier agents.",
    )
    standard_enabled_tool_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_STANDARD_ENABLED_TOOL_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of concurrently enabled tools allowed for standard tier agents.",
    )
    premium_enabled_tool_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_PREMIUM_ENABLED_TOOL_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of concurrently enabled tools allowed for premium tier agents.",
    )
    max_enabled_tool_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_MAX_ENABLED_TOOL_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of concurrently enabled tools allowed for max tier agents.",
    )
    ultra_enabled_tool_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_ULTRA_ENABLED_TOOL_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of concurrently enabled tools allowed for ultra tier agents.",
    )
    ultra_max_enabled_tool_limit = models.PositiveSmallIntegerField(
        default=DEFAULT_ULTRA_MAX_ENABLED_TOOL_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Number of concurrently enabled tools allowed for ultra max tier agents.",
    )
    standard_unified_history_limit = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Unified history event limit for standard tier agents.",
    )
    premium_unified_history_limit = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Unified history event limit for premium tier agents.",
    )
    max_unified_history_limit = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Unified history event limit for max tier agents.",
    )
    ultra_unified_history_limit = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Unified history event limit for ultra tier agents.",
    )
    ultra_max_unified_history_limit = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_LIMIT,
        validators=[MinValueValidator(1)],
        help_text="Unified history event limit for ultra max tier agents.",
    )
    standard_unified_history_hysteresis = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_HYSTERESIS,
        validators=[MinValueValidator(1)],
        help_text="Unified history hysteresis for standard tier agents.",
    )
    premium_unified_history_hysteresis = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_HYSTERESIS,
        validators=[MinValueValidator(1)],
        help_text="Unified history hysteresis for premium tier agents.",
    )
    max_unified_history_hysteresis = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_HYSTERESIS,
        validators=[MinValueValidator(1)],
        help_text="Unified history hysteresis for max tier agents.",
    )
    ultra_unified_history_hysteresis = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_HYSTERESIS,
        validators=[MinValueValidator(1)],
        help_text="Unified history hysteresis for ultra tier agents.",
    )
    ultra_max_unified_history_hysteresis = models.PositiveIntegerField(
        default=DEFAULT_UNIFIED_HISTORY_HYSTERESIS,
        validators=[MinValueValidator(1)],
        help_text="Unified history hysteresis for ultra max tier agents.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Prompt configuration"
        verbose_name_plural = "Prompt configuration"

    def save(self, *args, **kwargs):
        self.singleton_id = 1
        result = super().save(*args, **kwargs)
        from api.services.prompt_settings import invalidate_prompt_settings_cache

        invalidate_prompt_settings_cache()
        return result

    def delete(self, using=None, keep_parents=False):
        raise ValidationError("PromptConfig cannot be deleted.")

    def __str__(self):
        return "Prompt configuration"


class ToolCreditCost(models.Model):
    """Per-tool overrides for task credit consumption."""

    tool_name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Name of the tool (case-insensitive).",
    )
    credit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Credit cost charged when this tool is executed.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tool_name"]
        verbose_name = "Tool credit cost"
        verbose_name_plural = "Tool credit costs"

    def save(self, *args, **kwargs):  # pragma: no cover - exercised via util tests
        self.tool_name = (self.tool_name or "").strip().lower()
        if not self.tool_name:
            raise ValidationError({"tool_name": "Tool name cannot be blank."})

        result = super().save(*args, **kwargs)
        from util.tool_costs import clear_tool_credit_cost_cache

        clear_tool_credit_cost_cache()
        return result

    def delete(self, *args, **kwargs):  # pragma: no cover - exercised via util tests
        result = super().delete(*args, **kwargs)
        from util.tool_costs import clear_tool_credit_cost_cache

        clear_tool_credit_cost_cache()
        return result

    def __str__(self):
        return f"{self.tool_name} ({self.credit_cost} credits)"

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
            org_context = getattr(self, "_agent_creation_organization", None)
            if org_context is None:
                org_context = getattr(self, "_agent_creation_organization_id", None)

            owner = org_context if org_context is not None else self.user
            agents_available = AgentService.get_agents_available(owner)

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
    def _shared_proxy_queryset(cls):
        """Return proxies eligible for the shared pool (excluding actively allocated dedicated proxies)."""
        return ProxyServer.objects.filter(
            Q(is_dedicated=False) | Q(is_dedicated=True, dedicated_allocation__isnull=True),
            is_active=True,
        )

    @classmethod
    def _select_proxy_with_health_preference(cls):
        """Select proxy with preference for recent health check passes"""
        from datetime import timedelta
        from django.utils import timezone

        with traced("SELECT BrowserUseAgent Random Proxy") as span:
            # Consider health checks from the last 45 days as "recent"
            recent_cutoff = timezone.now() - timedelta(days=45)
            available_proxies = cls._shared_proxy_queryset()

            # First priority: Static IP proxies with recent successful health checks
            with traced("SELECT BrowserUseAgent Healthy Static Proxy"):
                healthy_static_proxy = available_proxies.filter(
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
                healthy_proxy = available_proxies.filter(
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
                static_ip_proxy = available_proxies.filter(
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
                proxy = available_proxies.order_by('?').first()

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

    eval_run = models.ForeignKey(
        "EvalRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="browser_tasks",
        help_text="Eval run that spawned this browser task, if any.",
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_tasks", null=True, blank=True)
    organization = models.ForeignKey(
        'Organization',
        on_delete=models.CASCADE,
        related_name='browser_use_tasks',
        null=True,
        blank=True,
        help_text="Owning organization, when applicable."
    )
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
    requires_vision = models.BooleanField(
        default=False,
        help_text="When true, restricts browser tasks to vision-capable LLM endpoints only.",
    )
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
    input_cost_total = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Total USD cost for prompt tokens (cached + uncached).",
    )
    input_cost_uncached = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="USD cost for uncached prompt tokens.",
    )
    input_cost_cached = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="USD cost for cached prompt tokens.",
    )
    output_cost = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="USD cost for completion tokens.",
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Total USD cost (input + output).",
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
    webhook_url = models.URLField(
        max_length=2048,
        null=True,
        blank=True,
        help_text="Callback URL invoked when the task reaches a terminal state.",
    )
    webhook_last_called_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent webhook attempt.",
    )
    webhook_last_status_code = models.IntegerField(
        null=True,
        blank=True,
        help_text="HTTP status code returned by the most recent webhook attempt.",
    )
    webhook_last_error = models.TextField(
        null=True,
        blank=True,
        help_text="Error message captured from the most recent webhook attempt, if any.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Fields for soft delete
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Billing rollup flag: has this task been included in a Stripe meter rollup?
    metered = models.BooleanField(default=False, db_index=True, help_text="Marked true once included in Stripe metering rollup.")
    # Temporary batch key used to reserve rows for an idempotent metering batch
    meter_batch_key = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    objects = BrowserUseAgentTaskQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at'], name='task_status_created_idx'),
            models.Index(fields=['created_at'], name='task_created_idx'),
            models.Index(fields=['organization', 'created_at'], name='task_org_created_idx'),
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

        owner_org = self.organization
        agent_org = None
        if self.agent:
            try:
                pa = self.agent.persistent_agent
            except Exception:
                pa = None
            else:
                if pa and getattr(pa, 'organization', None):
                    agent_org = pa.organization

        if owner_org is None and agent_org is not None:
            owner_org = agent_org
            self.organization = agent_org
        elif owner_org is not None and agent_org is not None and owner_org != agent_org:
            raise ValidationError({'organization': 'Organization mismatch between task and agent ownership.'})

        if self.organization_id is None and owner_org is not None:
            self.organization = owner_org

        if self._state.adding:
            if owner_org:
                task_credits = TaskCredit.objects.filter(
                    organization=owner_org, expiration_date__gte=timezone.now(), voided=False
                )
            else:
                task_credits = TaskCredit.objects.filter(
                    user=self.user, expiration_date__gte=timezone.now(), voided=False
                )

            available_tasks = sum(tc.remaining for tc in task_credits)
            subscription = get_active_subscription(self.user) if owner_org is None else None

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
                owner = None
                if self.organization_id:
                    owner = self.organization
                if owner is None and self.agent:
                    try:
                        pa = self.agent.persistent_agent
                    except Exception:
                        pa = None
                    else:
                        if pa and getattr(pa, 'organization', None):
                            owner = pa.organization

                if owner is None:
                    owner = self.user

                # Use consolidated credit checking and consumption logic (owner-aware)
                # Determine amount to consume; persist it on the task for auditability
                default_cost = get_default_task_credit_cost()
                persistent_agent = None
                if self.agent_id:
                    try:
                        persistent_agent = self.agent.persistent_agent
                    except Exception:
                        persistent_agent = None

                if self.credits_cost is not None:
                    amount = self.credits_cost
                else:
                    amount = default_cost
                    if persistent_agent is not None:
                        amount = _apply_tier_multiplier(persistent_agent, amount)
                    self.credits_cost = amount

                result = TaskCreditService.check_and_consume_credit_for_owner(owner, amount=amount)
                
                if not result['success']:
                    raise ValidationError({"quota": result['error_message']})
                
                # Associate the consumed credit with this task
                self.task_credit = result['credit']

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

            # Note: API keys are not auto-created on signup. Users must verify their
            # email before creating API keys (enforced in console/views.py ApiKeyListView).

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
    is_dedicated = models.BooleanField(
        default=False,
        help_text=(
            "True when this proxy can be allocated as dedicated inventory. "
            "Proxies with an active DedicatedProxyAllocation are withheld from the shared pool."
        ),
    )
    notes = models.TextField(blank=True, help_text="Additional notes about this proxy server")

    # Health check failure tracking
    consecutive_health_failures = models.PositiveIntegerField(
        default=0,
        help_text="Count of consecutive health check failures"
    )
    last_health_check_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent health check"
    )
    auto_deactivated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this proxy was automatically deactivated due to failures"
    )
    deactivation_reason = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text="Reason for deactivation (e.g., 'repeated_health_check_failures')"
    )

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
            models.Index(fields=['is_dedicated']),
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

    @property
    def is_dedicated_allocated(self) -> bool:
        """Return True when this dedicated proxy is currently assigned to an owner."""
        if not self.is_dedicated:
            return False
        try:
            allocation = self.dedicated_allocation
        except DedicatedProxyAllocation.DoesNotExist:
            return False
        except AttributeError:
            return False
        return allocation is not None

    def record_health_check(self, passed: bool) -> bool:
        """
        Record the result of a health check and potentially deactivate the proxy.

        Args:
            passed: Whether the health check passed

        Returns:
            True if the proxy was deactivated as a result of this check
        """
        from django.conf import settings
        from django.db.models import F

        now = timezone.now()
        self.last_health_check_at = now

        if passed:
            self.consecutive_health_failures = 0
            self.save(update_fields=['last_health_check_at', 'consecutive_health_failures'])
            return False

        # Atomically increment failure count to prevent race conditions
        ProxyServer.objects.filter(pk=self.pk).update(
            consecutive_health_failures=F('consecutive_health_failures') + 1
        )
        self.refresh_from_db(fields=['consecutive_health_failures'])

        deactivated = (
            self.consecutive_health_failures >= settings.PROXY_CONSECUTIVE_FAILURE_THRESHOLD
            and not self.is_dedicated_allocated
        )

        if deactivated:
            self.is_active = False
            self.auto_deactivated_at = now
            self.deactivation_reason = "repeated_health_check_failures"
            decodo_ip_id = self.decodo_ip_id
            update_fields = ['last_health_check_at', 'is_active', 'auto_deactivated_at', 'deactivation_reason']
            if decodo_ip_id:
                self.decodo_ip = None
                update_fields.append('decodo_ip')
            self.save(update_fields=update_fields)
            if decodo_ip_id:
                DecodoIP.objects.filter(id=decodo_ip_id).delete()
                from api.services.decodo_inventory import maybe_send_decodo_low_inventory_alert
                maybe_send_decodo_low_inventory_alert(reason="auto_deactivation")
        else:
            self.save(update_fields=['last_health_check_at'])

        return deactivated

    def set_dedicated_state(self, *, dedicated: bool, save: bool = True) -> None:
        """Toggle dedicated state with optional persistence hook."""
        self.is_dedicated = dedicated
        if save:
            self.save(update_fields=["is_dedicated", "updated_at"])


class DedicatedProxyAllocationQuerySet(models.QuerySet):
    def for_owner(self, owner):
        filters = DedicatedProxyAllocation._prepare_owner_filters(owner)
        return self.filter(**filters)


class DedicatedProxyAllocationManager(models.Manager.from_queryset(DedicatedProxyAllocationQuerySet)):  # type: ignore[misc]
    def assign_to_owner(self, proxy: 'ProxyServer', owner, *, notes: str | None = None):
        if proxy is None:
            raise ValueError("Proxy instance is required.")
        if owner is None:
            raise ValueError("Owner instance is required.")
        if not proxy.is_dedicated:
            raise ValidationError("Proxy must be marked dedicated before assignment.")
        if proxy.is_dedicated_allocated:
            raise ValidationError("Proxy already has a dedicated owner.")

        filters = DedicatedProxyAllocation._prepare_owner_filters(owner)
        allocation = self.model(proxy=proxy, **filters)
        if notes:
            allocation.notes = notes
        allocation.full_clean()
        allocation.save()
        return allocation


class DedicatedProxyAllocation(models.Model):
    """Ownership record for a dedicated proxy reserved to a user or organization."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proxy = models.OneToOneField(
        ProxyServer,
        on_delete=models.CASCADE,
        related_name="dedicated_allocation",
        help_text="Proxy reserved for this owner.",
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dedicated_proxy_allocations",
    )
    owner_organization = models.ForeignKey(
        'Organization',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dedicated_proxy_allocations",
    )
    notes = models.TextField(blank=True)
    allocated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DedicatedProxyAllocationManager()

    class Meta:
        ordering = ['-allocated_at']
        constraints = [
            models.CheckConstraint(
                check=(
                    (models.Q(owner_user__isnull=False) & models.Q(owner_organization__isnull=True))
                    |
                    (models.Q(owner_user__isnull=True) & models.Q(owner_organization__isnull=False))
                ),
                name='dedicated_proxy_single_owner',
            )
        ]
        indexes = [
            models.Index(fields=['owner_user']),
            models.Index(fields=['owner_organization']),
        ]

    def __str__(self) -> str:
        owner = self.owner
        return f"DedicatedProxyAllocation<{self.proxy_id}:{owner}>"

    @property
    def owner(self):
        return self.owner_user or self.owner_organization

    def clean(self):
        super().clean()
        if not self.proxy_id:
            raise ValidationError({"proxy": "Proxy is required."})
        if not self.proxy.is_dedicated:
            raise ValidationError({"proxy": "Proxy must be marked dedicated."})
        if bool(self.owner_user_id) == bool(self.owner_organization_id):
            raise ValidationError({"owner": "Dedicated proxies must be linked to exactly one owner."})

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    def release(self):
        """Release this allocation back to the pool."""
        self.delete()

    @staticmethod
    def _prepare_owner_filters(owner):
        from django.contrib.auth import get_user_model
        from django.apps import apps

        UserModel = get_user_model()
        Organization = apps.get_model("api", "Organization")

        if isinstance(owner, UserModel):
            return {"owner_user": owner}
        if isinstance(owner, Organization):
            return {"owner_organization": owner}

        raise TypeError(f"Unsupported owner type: {owner.__class__.__name__}")


# --------------------------------------------------------------------------- #
#  LLM Provider + Endpoint Config (DB-managed load balancing/failover)
# --------------------------------------------------------------------------- #

class LLMProvider(models.Model):
    """Vendor-level provider configuration and credentials.

    Credentials may come from an encrypted admin-set value or an environment
    variable (env_var_name). At runtime, the effective key is chosen as
    admin-set if present, otherwise from env.
    """

    class BrowserBackend(models.TextChoices):
        OPENAI = "OPENAI", "OpenAI"
        ANTHROPIC = "ANTHROPIC", "Anthropic"
        GOOGLE = "GOOGLE", "Google"
        OPENAI_COMPAT = "OPENAI_COMPAT", "OpenAI-Compatible"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=64, unique=True, help_text="Provider key, e.g., 'openai', 'anthropic'")
    display_name = models.CharField(max_length=128)
    enabled = models.BooleanField(default=True)

    # Credentials
    api_key_encrypted = models.BinaryField(null=True, blank=True, help_text="AES-256-GCM encrypted API key (optional)")
    env_var_name = models.CharField(max_length=128, blank=True, help_text="Environment variable fallback for API key")

    model_prefix = models.CharField(
        max_length=64,
        blank=True,
        help_text="Optional prefix automatically added to model identifiers (e.g., 'openrouter/').",
    )

    # Provider-wide options
    supports_safety_identifier = models.BooleanField(default=False)
    browser_backend = models.CharField(
        max_length=16,
        choices=BrowserBackend.choices,
        default=BrowserBackend.OPENAI,
        help_text="Browser client backend to use for this provider"
    )

    # Google Vertex specifics (optional)
    vertex_project = models.CharField(max_length=128, blank=True)
    vertex_location = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["enabled"]),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.key})"


class PersistentModelEndpoint(models.Model):
    """Model endpoint for persistent agents (LiteLLM)."""

    class ReasoningEffort(models.TextChoices):
        MINIMAL = "minimal", "Minimal"
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=96, unique=True, help_text="Endpoint key, e.g., 'openai_gpt5'")
    provider = models.ForeignKey(LLMProvider, on_delete=models.CASCADE, related_name="persistent_endpoints")
    enabled = models.BooleanField(default=True)
    low_latency = models.BooleanField(
        default=False,
        help_text="Marks this endpoint as low latency/high performance.",
    )

    # LiteLLM model string and options
    litellm_model = models.CharField(max_length=256)
    temperature_override = models.FloatField(null=True, blank=True)
    supports_temperature = models.BooleanField(
        default=True,
        help_text="Indicates whether this model accepts a temperature parameter",
    )
    supports_tool_choice = models.BooleanField(default=True)
    use_parallel_tool_calls = models.BooleanField(default=True)
    supports_vision = models.BooleanField(
        default=False,
        help_text="Indicates the model can process image or multimodal inputs",
    )
    supports_reasoning = models.BooleanField(
        default=False,
        help_text="Indicates the model accepts reasoning parameters",
    )
    reasoning_effort = models.CharField(
        max_length=16,
        choices=ReasoningEffort.choices,
        null=True,
        blank=True,
        default=None,
        help_text="Default reasoning effort to pass when reasoning is supported",
    )
    # For OpenAI-compatible endpoints via LiteLLM (model startswith 'openai/...')
    # provide the custom base URL used by your proxy (e.g., http://vllm-host:port/v1)
    api_base = models.CharField(max_length=256, blank=True)
    openrouter_preset = models.CharField(
        max_length=128,
        blank=True,
        help_text="Optional OpenRouter preset identifier applied to this endpoint.",
    )
    max_input_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum input tokens for this endpoint. Leave blank for automatic (no limit).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider__display_name", "litellm_model"]
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["enabled"]),
            models.Index(fields=["provider"]),
        ]

    def __str__(self):
        return f"{self.key} → {self.litellm_model}"


class PersistentTokenRange(models.Model):
    """Token ranges for selecting persistent LLM tiers."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64, unique=True)
    min_tokens = models.PositiveIntegerField()
    max_tokens = models.PositiveIntegerField(null=True, blank=True, help_text="Exclusive upper bound; null means infinity")

    class Meta:
        ordering = ["min_tokens"]
        indexes = [
            models.Index(fields=["min_tokens", "max_tokens"]),
        ]

    def __str__(self):
        upper = "∞" if self.max_tokens is None else str(self.max_tokens)
        return f"{self.name} [{self.min_tokens}, {upper})"


class PersistentLLMTier(models.Model):
    """Tier within a token range for persistent agents."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_range = models.ForeignKey(PersistentTokenRange, on_delete=models.CASCADE, related_name="tiers")
    order = models.PositiveIntegerField(help_text="1-based order within the range")
    description = models.CharField(max_length=256, blank=True)
    intelligence_tier = models.ForeignKey(
        IntelligenceTier,
        on_delete=models.PROTECT,
        related_name="persistent_tiers",
        default=_get_default_intelligence_tier_id,
    )

    class Meta:
        ordering = ["token_range__min_tokens", "intelligence_tier__rank", "order"]
        unique_together = (("token_range", "order", "intelligence_tier"),)

    def __str__(self):
        tier_key = getattr(self.intelligence_tier, "key", "standard")
        return f"{self.token_range.name} {tier_key} tier {self.order}"

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        _invalidate_tier_multiplier_cache()
        return result

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        _invalidate_tier_multiplier_cache()
        return result


class PersistentTierEndpoint(models.Model):
    """Weighted association between a Persistent tier and a model endpoint."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(PersistentLLMTier, on_delete=models.CASCADE, related_name="tier_endpoints")
    endpoint = models.ForeignKey(PersistentModelEndpoint, on_delete=models.CASCADE, related_name="in_tiers")
    weight = models.FloatField(help_text="Relative weight within the tier; > 0")
    reasoning_effort_override = models.CharField(
        max_length=16,
        choices=PersistentModelEndpoint.ReasoningEffort.choices,
        null=True,
        blank=True,
        default=None,
        help_text="Optional reasoning effort override applied when the endpoint supports reasoning.",
    )
    class Meta:
        ordering = ["tier__order", "endpoint__key"]
        unique_together = (("tier", "endpoint"),)

    def __str__(self):
        tier_key = getattr(self.tier.intelligence_tier, "key", "standard")
        return f"{self.tier} → {self.endpoint.key} [{tier_key}] (w={self.weight})"


class EmbeddingsModelEndpoint(models.Model):
    """Embeddings endpoint configuration used for text similarity scoring."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=96, unique=True, help_text="Endpoint key, e.g., 'openai_text_embed_small'")
    provider = models.ForeignKey(
        LLMProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="embedding_endpoints",
        help_text="Optional link to the provider supplying credentials for this endpoint.",
    )
    enabled = models.BooleanField(default=True)
    low_latency = models.BooleanField(
        default=False,
        help_text="Marks this endpoint as low latency/high performance.",
    )

    litellm_model = models.CharField(max_length=256, help_text="Model identifier passed to LiteLLM for embeddings.")
    api_base = models.CharField(
        max_length=256,
        blank=True,
        help_text="Optional OpenAI-compatible base URL for proxy endpoints.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider__display_name", "litellm_model"]
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["enabled"]),
            models.Index(fields=["provider"]),
        ]

    def __str__(self):
        provider = self.provider.display_name if self.provider else "no-provider"
        return f"{self.key} → {self.litellm_model} ({provider})"


class EmbeddingsLLMTier(models.Model):
    """Fallback tier ordering for embeddings endpoints."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.PositiveIntegerField(unique=True, help_text="1-based order across all embedding tiers.")
    description = models.CharField(max_length=256, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"Tier {self.order}"


class EmbeddingsTierEndpoint(models.Model):
    """Weighted association between an embeddings tier and an endpoint."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(
        EmbeddingsLLMTier,
        on_delete=models.CASCADE,
        related_name="tier_endpoints",
    )
    endpoint = models.ForeignKey(
        EmbeddingsModelEndpoint,
        on_delete=models.CASCADE,
        related_name="in_tiers",
    )
    weight = models.FloatField(help_text="Relative weight within the tier; must be > 0.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier__order", "endpoint__key"]
        unique_together = (("tier", "endpoint"),)

    def __str__(self):
        return f"{self.tier} → {self.endpoint.key} (w={self.weight})"


class FileHandlerModelEndpoint(models.Model):
    """File handler endpoint configuration used for file-to-markdown conversion."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=96, unique=True, help_text="Endpoint key, e.g., 'openai_gpt4o_file_handler'")
    provider = models.ForeignKey(
        LLMProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="file_handler_endpoints",
        help_text="Optional link to the provider supplying credentials for this endpoint.",
    )
    enabled = models.BooleanField(default=True)
    low_latency = models.BooleanField(
        default=False,
        help_text="Marks this endpoint as low latency/high performance.",
    )

    litellm_model = models.CharField(max_length=256, help_text="Model identifier passed to LiteLLM.")
    api_base = models.CharField(
        max_length=256,
        blank=True,
        help_text="Optional OpenAI-compatible base URL for proxy endpoints.",
    )
    supports_vision = models.BooleanField(
        default=False,
        help_text="Indicates the model can process image or multimodal inputs.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider__display_name", "litellm_model"]
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["enabled"]),
            models.Index(fields=["provider"]),
        ]

    def __str__(self):
        provider = self.provider.display_name if self.provider else "no-provider"
        return f"{self.key} → {self.litellm_model} ({provider})"


class FileHandlerLLMTier(models.Model):
    """Fallback tier ordering for file handler endpoints."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.PositiveIntegerField(unique=True, help_text="1-based order across all file handler tiers.")
    description = models.CharField(max_length=256, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"Tier {self.order}"


class FileHandlerTierEndpoint(models.Model):
    """Weighted association between a file handler tier and an endpoint."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(
        FileHandlerLLMTier,
        on_delete=models.CASCADE,
        related_name="tier_endpoints",
    )
    endpoint = models.ForeignKey(
        FileHandlerModelEndpoint,
        on_delete=models.CASCADE,
        related_name="in_tiers",
    )
    weight = models.FloatField(help_text="Relative weight within the tier; must be > 0.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier__order", "endpoint__key"]
        unique_together = (("tier", "endpoint"),)

    def __str__(self):
        return f"{self.tier} → {self.endpoint.key} (w={self.weight})"


class SummarizationModelEndpoint(models.Model):
    """Summarization endpoint configuration used for text summarization tasks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=96, unique=True, help_text="Endpoint key, e.g., 'openai_gpt4o_summary'")
    provider = models.ForeignKey(
        LLMProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="summarization_endpoints",
        help_text="Optional link to the provider supplying credentials for this endpoint.",
    )
    enabled = models.BooleanField(default=True)
    low_latency = models.BooleanField(
        default=False,
        help_text="Marks this endpoint as low latency/high performance.",
    )

    litellm_model = models.CharField(max_length=256, help_text="Model identifier passed to LiteLLM.")
    api_base = models.CharField(
        max_length=256,
        blank=True,
        help_text="Optional OpenAI-compatible base URL for proxy endpoints.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider__display_name", "litellm_model"]
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["enabled"]),
            models.Index(fields=["provider"]),
        ]

    def __str__(self):
        provider = self.provider.display_name if self.provider else "no-provider"
        return f"{self.key} → {self.litellm_model} ({provider})"


class SummarizationLLMTier(models.Model):
    """Fallback tier ordering for summarization endpoints."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.PositiveIntegerField(unique=True, help_text="1-based order across all summarization tiers.")
    description = models.CharField(max_length=256, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"Tier {self.order}"


class SummarizationTierEndpoint(models.Model):
    """Weighted association between a summarization tier and an endpoint."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(
        SummarizationLLMTier,
        on_delete=models.CASCADE,
        related_name="tier_endpoints",
    )
    endpoint = models.ForeignKey(
        SummarizationModelEndpoint,
        on_delete=models.CASCADE,
        related_name="in_tiers",
    )
    weight = models.FloatField(help_text="Relative weight within the tier; must be > 0.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier__order", "endpoint__key"]
        unique_together = (("tier", "endpoint"),)

    def __str__(self):
        return f"{self.tier} → {self.endpoint.key} (w={self.weight})"


class BrowserModelEndpoint(models.Model):
    """Model endpoint for browser-use agents (Chat clients)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=96, unique=True, help_text="Endpoint key, e.g., 'openrouter_glm_45'")
    provider = models.ForeignKey(LLMProvider, on_delete=models.CASCADE, related_name="browser_endpoints")
    enabled = models.BooleanField(default=True)
    low_latency = models.BooleanField(
        default=False,
        help_text="Marks this endpoint as low latency/high performance.",
    )

    browser_model = models.CharField(max_length=256)
    browser_base_url = models.CharField(max_length=256, blank=True, help_text="Base URL for OpenAI-compatible providers (optional)")
    max_output_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional override of the provider's max output tokens; null disables the override.",
    )
    supports_temperature = models.BooleanField(
        default=True,
        help_text="Indicates whether this model accepts a temperature parameter",
    )
    supports_vision = models.BooleanField(
        default=False,
        help_text="Indicates the model can process image or multimodal inputs",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider__display_name", "browser_model"]
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["enabled"]),
            models.Index(fields=["provider"]),
        ]

    def __str__(self):
        return f"{self.key} → {self.browser_model}"


class BrowserLLMPolicy(models.Model):
    """Active browser-use LLM policy (tiers)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}{' (active)' if self.is_active else ''}"


class BrowserLLMTier(models.Model):
    """Tier within a browser-use policy."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.ForeignKey(BrowserLLMPolicy, on_delete=models.CASCADE, related_name="tiers")
    order = models.PositiveIntegerField(help_text="1-based order within the policy")
    description = models.CharField(max_length=256, blank=True)
    intelligence_tier = models.ForeignKey(
        IntelligenceTier,
        on_delete=models.PROTECT,
        related_name="browser_tiers",
        default=_get_default_intelligence_tier_id,
    )

    class Meta:
        ordering = ["policy__name", "intelligence_tier__rank", "order"]
        unique_together = (("policy", "order", "intelligence_tier"),)

    def __str__(self):
        tier_key = getattr(self.intelligence_tier, "key", "standard")
        return f"{self.policy.name} {tier_key} tier {self.order}"


class BrowserTierEndpoint(models.Model):
    """Weighted association between a Browser tier and a model endpoint."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(BrowserLLMTier, on_delete=models.CASCADE, related_name="tier_endpoints")
    endpoint = models.ForeignKey(BrowserModelEndpoint, on_delete=models.CASCADE, related_name="in_tiers")
    extraction_endpoint = models.ForeignKey(
        BrowserModelEndpoint,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="as_extraction_in_tiers",
        help_text="Optional paired endpoint used for page extraction LLM calls.",
    )
    weight = models.FloatField(help_text="Relative weight within the tier; > 0")

    class Meta:
        ordering = ["tier__order", "endpoint__key"]
        unique_together = (("tier", "endpoint"),)

    def __str__(self):
        tier_key = getattr(self.tier.intelligence_tier, "key", "standard")
        return f"{self.tier} → {self.endpoint.key} [{tier_key}] (w={self.weight})"


# --------------------------------------------------------------------------- #
#  LLM Routing Profiles (switchable config containers for failover/tiers)
# --------------------------------------------------------------------------- #

class LLMRoutingProfile(models.Model):
    """Top-level container for a complete LLM routing configuration.

    A routing profile groups together the full failover/tier configuration for:
    - Persistent agents (token-range-based tiers)
    - Browser agents (policy-based tiers)
    - Embeddings (simple tier ordering)

    Only one profile can be active at a time for runtime routing. Evals can
    override the active profile by specifying a profile on EvalSuiteRun.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.SlugField(
        max_length=64,
        unique=True,
        help_text="URL-safe identifier, e.g., 'production-v3', 'eval-gpt5-only'",
    )
    display_name = models.CharField(max_length=128)
    description = models.TextField(blank=True)

    is_active = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Active profile used for all runtime routing. Only one can be active.",
    )
    is_eval_snapshot = models.BooleanField(
        default=False,
        db_index=True,
        help_text="If true, this is a frozen snapshot created for an eval run. Not editable.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_llm_profiles",
    )
    cloned_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clones",
        help_text="Source profile this was cloned from, if any.",
    )

    eval_judge_endpoint = models.ForeignKey(
        "PersistentModelEndpoint",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="eval_judge_profiles",
        help_text="Endpoint used for eval judging/grading. If null, uses default from tier config.",
    )

    class Meta:
        ordering = ["-is_active", "display_name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["is_eval_snapshot"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=Q(is_active=True),
                name="unique_active_llm_routing_profile",
            )
        ]

    def __str__(self):
        suffix = " (active)" if self.is_active else ""
        return f"{self.display_name}{suffix}"


class ProfileTokenRange(models.Model):
    """Token range within a routing profile for persistent agent tier selection."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(
        LLMRoutingProfile,
        on_delete=models.CASCADE,
        related_name="persistent_token_ranges",
    )
    name = models.CharField(max_length=64, help_text="Range name, e.g., 'small', 'medium', 'large'")
    min_tokens = models.PositiveIntegerField()
    max_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Exclusive upper bound; null means infinity",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["profile", "min_tokens"]
        unique_together = [("profile", "name")]
        indexes = [
            models.Index(fields=["profile", "min_tokens", "max_tokens"]),
        ]

    def __str__(self):
        upper = "∞" if self.max_tokens is None else str(self.max_tokens)
        return f"{self.profile.name}:{self.name} [{self.min_tokens}, {upper})"


class ProfilePersistentTier(models.Model):
    """Failover tier within a profile's token range for persistent agents."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_range = models.ForeignKey(
        ProfileTokenRange,
        on_delete=models.CASCADE,
        related_name="tiers",
    )
    order = models.PositiveIntegerField(help_text="1-based order within the range")
    description = models.CharField(max_length=256, blank=True)
    intelligence_tier = models.ForeignKey(
        IntelligenceTier,
        on_delete=models.PROTECT,
        related_name="profile_persistent_tiers",
        default=_get_default_intelligence_tier_id,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["token_range__profile", "token_range__min_tokens", "intelligence_tier__rank", "order"]
        unique_together = [("token_range", "order", "intelligence_tier")]

    def __str__(self):
        tier_key = getattr(self.intelligence_tier, "key", "standard")
        return f"{self.token_range} {tier_key} tier {self.order}"


class ProfilePersistentTierEndpoint(models.Model):
    """Weighted endpoint assignment within a profile's persistent tier."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(
        ProfilePersistentTier,
        on_delete=models.CASCADE,
        related_name="tier_endpoints",
    )
    endpoint = models.ForeignKey(
        PersistentModelEndpoint,
        on_delete=models.CASCADE,
        related_name="in_profile_tiers",
    )
    weight = models.FloatField(help_text="Relative weight within the tier; must be > 0.")
    reasoning_effort_override = models.CharField(
        max_length=16,
        choices=PersistentModelEndpoint.ReasoningEffort.choices,
        null=True,
        blank=True,
        default=None,
        help_text="Optional reasoning effort override applied when the endpoint supports reasoning.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier__order", "endpoint__key"]
        unique_together = [("tier", "endpoint")]

    def __str__(self):
        tier_key = getattr(self.tier.intelligence_tier, "key", "standard")
        return f"{self.tier} → {self.endpoint.key} [{tier_key}] (w={self.weight})"


class ProfileBrowserTier(models.Model):
    """Browser agent failover tier within a routing profile."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(
        LLMRoutingProfile,
        on_delete=models.CASCADE,
        related_name="browser_tiers",
    )
    order = models.PositiveIntegerField(help_text="1-based order within the profile")
    description = models.CharField(max_length=256, blank=True)
    intelligence_tier = models.ForeignKey(
        IntelligenceTier,
        on_delete=models.PROTECT,
        related_name="profile_browser_tiers",
        default=_get_default_intelligence_tier_id,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["profile", "intelligence_tier__rank", "order"]
        unique_together = [("profile", "order", "intelligence_tier")]

    def __str__(self):
        tier_key = getattr(self.intelligence_tier, "key", "standard")
        return f"{self.profile.name} browser {tier_key} tier {self.order}"


class ProfileBrowserTierEndpoint(models.Model):
    """Weighted endpoint assignment within a profile's browser tier."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(
        ProfileBrowserTier,
        on_delete=models.CASCADE,
        related_name="tier_endpoints",
    )
    endpoint = models.ForeignKey(
        BrowserModelEndpoint,
        on_delete=models.CASCADE,
        related_name="in_profile_tiers",
    )
    extraction_endpoint = models.ForeignKey(
        BrowserModelEndpoint,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="as_extraction_in_profile_tiers",
        help_text="Optional paired endpoint used for page extraction LLM calls.",
    )
    weight = models.FloatField(help_text="Relative weight within the tier; must be > 0.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier__order", "endpoint__key"]
        unique_together = [("tier", "endpoint")]

    def __str__(self):
        tier_key = getattr(self.tier.intelligence_tier, "key", "standard")
        return f"{self.tier} → {self.endpoint.key} [{tier_key}] (w={self.weight})"


class ProfileEmbeddingsTier(models.Model):
    """Embeddings failover tier within a routing profile."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(
        LLMRoutingProfile,
        on_delete=models.CASCADE,
        related_name="embeddings_tiers",
    )
    order = models.PositiveIntegerField(help_text="1-based order within the profile")
    description = models.CharField(max_length=256, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["profile", "order"]
        unique_together = [("profile", "order")]

    def __str__(self):
        return f"{self.profile.name} embeddings tier {self.order}"


class ProfileEmbeddingsTierEndpoint(models.Model):
    """Weighted endpoint assignment within a profile's embeddings tier."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(
        ProfileEmbeddingsTier,
        on_delete=models.CASCADE,
        related_name="tier_endpoints",
    )
    endpoint = models.ForeignKey(
        EmbeddingsModelEndpoint,
        on_delete=models.CASCADE,
        related_name="in_profile_tiers",
    )
    weight = models.FloatField(help_text="Relative weight within the tier; must be > 0.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier__order", "endpoint__key"]
        unique_together = [("tier", "endpoint")]

    def __str__(self):
        return f"{self.tier} → {self.endpoint.key} (w={self.weight})"


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


class DecodoLowInventoryAlert(models.Model):
    """Record low-inventory alert sends for Decodo proxy capacity."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sent_on = models.DateField(help_text="Local date when the alert was sent.")
    active_proxy_count = models.PositiveIntegerField(
        help_text="Active Decodo proxies available (excluding dedicated allocations).",
    )
    threshold = models.PositiveIntegerField(
        help_text="Inventory threshold that triggered the alert.",
    )
    recipient_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["sent_on"],
                name="unique_decodo_low_inventory_alert_day",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"DecodoLowInventoryAlert<{self.sent_on}: {self.active_proxy_count}>"

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
        choices=UserPlanNamesChoices.choices,
        default=PlanNames.FREE,
        help_text="The user's subscription plan"
    )
    plan_version = models.ForeignKey(
        PlanVersion,
        on_delete=models.SET_NULL,
        related_name="user_billings",
        null=True,
        blank=True,
        help_text="Resolved plan version for this billing record.",
    )
    max_extra_tasks = models.IntegerField(
        default=0,
        help_text="Maximum number of additional tasks allowed beyond plan limits. 0 means no extra tasks, -1 means unlimited.",
    )
    max_contacts_per_agent = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "If set, overrides the plan's max contacts per agent for this user. "
            "Leave blank to use the default from the subscription plan."
        ),
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


class AddonEntitlementQuerySet(models.QuerySet):
    def for_owner(self, owner):
        if owner is None:
            return self.none()

        Organization = apps.get_model("api", "Organization")
        if isinstance(owner, Organization):
            return self.filter(organization=owner)

        return self.filter(user=owner)

    def active(self, at_time=None):
        if at_time is None:
            at_time = timezone.now()

        return self.filter(
            models.Q(starts_at__lte=at_time) | models.Q(starts_at__isnull=True),
            models.Q(expires_at__gt=at_time) | models.Q(expires_at__isnull=True),
        )


class AddonEntitlement(models.Model):
    """Purchased add-ons that uplift usage limits or enable premium features."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="addon_entitlements",
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        "api.Organization",
        on_delete=models.CASCADE,
        related_name="addon_entitlements",
        null=True,
        blank=True,
    )
    product_id = models.CharField(max_length=255, blank=True, default="")
    price_id = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    task_credits_delta = models.IntegerField(
        default=0,
        help_text="Per-unit additional task credits granted for the billing cycle.",
    )
    contact_cap_delta = models.PositiveIntegerField(
        default=0,
        help_text="Per-unit increase to max contacts per agent.",
    )
    browser_task_daily_delta = models.PositiveIntegerField(
        default=0,
        help_text="Per-unit increase to per-agent daily browser task limit.",
    )
    advanced_captcha_resolution_delta = models.PositiveIntegerField(
        default=0,
        help_text="Per-unit enablement of advanced CAPTCHA resolution for browser tasks.",
    )
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_recurring = models.BooleanField(default=False)
    created_via = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AddonEntitlementQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Add-on entitlement"
        verbose_name_plural = "Add-on entitlements"
        constraints = [
            models.CheckConstraint(
                check=(
                    (
                        (models.Q(user__isnull=False) & models.Q(organization__isnull=True))
                        | (models.Q(user__isnull=True) & models.Q(organization__isnull=False))
                    )
                ),
                name="addon_entitlement_owner_present",
            ),
        ]

    def __str__(self) -> str:
        target = self.organization or self.user
        return f"AddonEntitlement<{target}> x{self.quantity}"

    @property
    def owner(self):
        return self.organization or self.user


class UserAttribution(models.Model):
    """Persist first/last touch attribution details for a user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attribution",
    )

    utm_source_first = models.CharField(max_length=256, blank=True)
    utm_medium_first = models.CharField(max_length=256, blank=True)
    utm_campaign_first = models.CharField(max_length=256, blank=True)
    utm_content_first = models.CharField(max_length=256, blank=True)
    utm_term_first = models.CharField(max_length=256, blank=True)

    utm_source_last = models.CharField(max_length=256, blank=True)
    utm_medium_last = models.CharField(max_length=256, blank=True)
    utm_campaign_last = models.CharField(max_length=256, blank=True)
    utm_content_last = models.CharField(max_length=256, blank=True)
    utm_term_last = models.CharField(max_length=256, blank=True)

    landing_code_first = models.CharField(max_length=128, blank=True)
    landing_code_last = models.CharField(max_length=128, blank=True)

    fbclid = models.CharField(max_length=1024, blank=True)
    fbc = models.CharField(max_length=1024, blank=True)
    gclid_first = models.CharField(max_length=256, blank=True)
    gclid_last = models.CharField(max_length=256, blank=True)
    gbraid_first = models.CharField(max_length=256, blank=True)
    gbraid_last = models.CharField(max_length=256, blank=True)
    wbraid_first = models.CharField(max_length=256, blank=True)
    wbraid_last = models.CharField(max_length=256, blank=True)
    msclkid_first = models.CharField(max_length=256, blank=True)
    msclkid_last = models.CharField(max_length=256, blank=True)
    ttclid_first = models.CharField(max_length=256, blank=True)
    ttclid_last = models.CharField(max_length=256, blank=True)

    first_referrer = models.CharField(max_length=512, blank=True)
    last_referrer = models.CharField(max_length=512, blank=True)
    first_landing_path = models.CharField(max_length=512, blank=True)
    last_landing_path = models.CharField(max_length=512, blank=True)

    segment_anonymous_id = models.CharField(max_length=256, blank=True)
    ga_client_id = models.CharField(max_length=256, blank=True)

    first_touch_at = models.DateTimeField(null=True, blank=True)
    last_touch_at = models.DateTimeField(null=True, blank=True)
    last_client_ip = models.GenericIPAddressField(null=True, blank=True, help_text="Most recent client IP observed for this user.")
    last_user_agent = models.TextField(blank=True, default='', help_text="Most recent user agent observed for this user.")
    fbp = models.CharField(max_length=256, blank=True, default='', help_text="Meta Browser ID (_fbp cookie value).")

    # Referral tracking: who referred this user at signup
    referrer_code = models.CharField(
        max_length=32,
        blank=True,
        default='',
        db_index=True,
        help_text="Referral code used at signup (direct referral from another user)"
    )
    signup_template_code = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text="Template code if user signed up via a shared agent template"
    )
    referral_credit_granted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When referral credits were granted to the referrer (null if pending or N/A)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Attribution"
        verbose_name_plural = "User Attribution"

    def __str__(self):
        return f"Attribution for user {self.user_id}"


class ReferralGrant(models.Model):
    """Audit record for referral credit grants."""

    class ReferralTypeChoices(models.TextChoices):
        DIRECT = "direct", "Direct"
        TEMPLATE = "template", "Template"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referral_grants_made",
    )
    referred = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referral_grant",
        help_text="User who received the referral incentive.",
    )
    referral_type = models.CharField(
        max_length=16,
        choices=ReferralTypeChoices.choices,
        help_text="Referral source type (direct or template).",
    )
    template_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Template code if this was a shared-template referral.",
    )
    referrer_task_credit = models.ForeignKey(
        "TaskCredit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referrer_referral_grants",
    )
    referred_task_credit = models.ForeignKey(
        "TaskCredit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referred_referral_grants",
    )
    granted_at = models.DateTimeField(help_text="When the referral grant was processed.")
    config_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Referral incentive configuration snapshot used for this grant.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-granted_at"]
        verbose_name = "Referral grant"
        verbose_name_plural = "Referral grants"

    def __str__(self):
        return f"ReferralGrant<{self.referred_id}:{self.referral_type}>"


class OrganizationBilling(models.Model):
    """Billing data for an organization (mirrors the user billing fields where applicable)."""

    organization = models.OneToOneField(
        'Organization',
        on_delete=models.CASCADE,
        related_name='billing',
    )
    subscription = models.CharField(
        max_length=32,
        choices=OrganizationPlanNamesChoices.choices,
        default=PlanNames.FREE,
        help_text="The organization's subscription plan",
    )
    plan_version = models.ForeignKey(
        PlanVersion,
        on_delete=models.SET_NULL,
        related_name="organization_billings",
        null=True,
        blank=True,
        help_text="Resolved plan version for this billing record.",
    )
    billing_cycle_anchor = models.IntegerField(
        default=1,
        help_text="Day of the month when billing cycle starts (1-31).",
        validators=[
            MinValueValidator(1),
            MaxValueValidator(31),
        ],
    )
    stripe_customer_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Stripe customer identifier for the organization",
    )
    stripe_subscription_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Stripe subscription identifier for the organization",
    )
    cancel_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the subscription is scheduled to cancel",
    )
    cancel_at_period_end = models.BooleanField(
        default=False,
        help_text="Whether the subscription will cancel at the end of the period",
    )
    downgraded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the organization was downgraded to free",
    )
    purchased_seats = models.PositiveIntegerField(
        default=0,
        help_text="Number of seats purchased for this organization (must cover active members + pending invites beyond the founder).",
    )
    pending_seat_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Seat quantity scheduled to take effect in a future billing period.",
    )
    pending_seat_effective_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the pending seat quantity is expected to take effect.",
    )
    pending_seat_schedule_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Stripe subscription schedule ID managing the pending seat change.",
    )
    max_extra_tasks = models.IntegerField(
        default=0,
        help_text="Maximum number of additional tasks the org can buy beyond included credits. 0 means disabled; -1 is unlimited.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Billing for organization {self.organization_id}"

    def clean(self):
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        super().clean()

        if self.organization_id is None:
            return

        now = timezone.now()

        founder_allowance = 1

        active_members = OrganizationMembership.objects.filter(
            org_id=self.organization_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).exclude(role=OrganizationMembership.OrgRole.SOLUTIONS_PARTNER).count()
        pending_invites = OrganizationInvite.objects.filter(
            org_id=self.organization_id,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gte=now,
        ).exclude(role=OrganizationMembership.OrgRole.SOLUTIONS_PARTNER).count()

        seats_required = max(active_members - founder_allowance, 0) + pending_invites

        if self.purchased_seats < seats_required:
            raise ValidationError({
                "purchased_seats": (
                    "Cannot set purchased seats below the number currently reserved ("
                    f"{seats_required}). Increase seats or remove members/invites first."
                )
            })

    @property
    def seats_reserved(self) -> int:
        from django.utils import timezone

        if self.organization_id is None:
            return 0

        now = timezone.now()

        founder_allowance = 1

        active_members = OrganizationMembership.objects.filter(
            org_id=self.organization_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).exclude(role=OrganizationMembership.OrgRole.SOLUTIONS_PARTNER).count()
        pending_invites = OrganizationInvite.objects.filter(
            org_id=self.organization_id,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gte=now,
        ).exclude(role=OrganizationMembership.OrgRole.SOLUTIONS_PARTNER).count()
        reserved_members = max(active_members - founder_allowance, 0)
        return reserved_members + pending_invites

    @property
    def seats_available(self) -> int:
        return max(self.purchased_seats - self.seats_reserved, 0)

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Organization Billing"
        verbose_name_plural = "Organization Billing"


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

class StripeConfig(models.Model):
    """Per-environment Stripe credentials and identifiers."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    release_env = models.CharField(
        max_length=32,
        unique=True,
        help_text="Environment this configuration applies to (e.g., prod, staging, local).",
    )
    live_mode = models.BooleanField(
        default=False,
        help_text="Whether this configuration should run Stripe in live mode.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["release_env"]
        verbose_name = "Stripe Configuration"
        verbose_name_plural = "Stripe Configuration"

    def __str__(self) -> str:
        return f"StripeConfig<{self.release_env}>"

    def _entries_cache(self) -> dict[str, "StripeConfigEntry"]:
        if not hasattr(self, "_entries_by_name"):
            prefetched = getattr(self, "_prefetched_objects_cache", {})
            if "entries" in prefetched:
                entries = prefetched["entries"]
            else:
                entries = list(self.entries.all())
            self._entries_by_name = {entry.name: entry for entry in entries}
        return self._entries_by_name

    def _get_entry(self, name: str) -> "StripeConfigEntry | None":
        return self._entries_cache().get(name)

    def get_value(self, name: str, default: str = "") -> str:
        entry = self._get_entry(name)
        if entry is None:
            return default
        return entry.get_value()

    def set_value(self, name: str, value: str | None, *, is_secret: bool = False) -> None:
        entry = self._get_entry(name)
        if entry is None:
            entry = StripeConfigEntry(config=self, name=name, is_secret=is_secret)
            created = True
        else:
            created = False
        entry.is_secret = is_secret
        entry.set_value(value)
        if created:
            entry.save()
        else:
            entry.save(update_fields=["value_text", "value_encrypted", "is_secret", "updated_at"])
        self._entries_cache()[name] = entry

    def clear_value(self, name: str) -> None:
        entry = self._get_entry(name)
        if entry is None:
            return
        entry.set_value(None)
        entry.save(update_fields=["value_text", "value_encrypted", "updated_at"])
        self._entries_cache()[name] = entry

    def has_value(self, name: str) -> bool:
        entry = self._get_entry(name)
        if entry is None:
            return False
        return entry.has_value

    @staticmethod
    def _parse_list_value(raw: str | None) -> list[str]:
        if not raw:
            return []
        value: list[str] = []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, (list, tuple, set)):
                value = list(parsed)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = []
        if not value:
            value = [part.strip() for part in str(raw).split(",")]
        return [item for item in (str(s).strip() for s in value if s) if item]

    def _set_list_value(self, name: str, value: list[str] | tuple[str, ...] | str | None) -> None:
        if value is None:
            self.set_value(name, None)
            return
        if isinstance(value, (list, tuple, set)):
            joined = ",".join(str(item).strip() for item in value if item)
        else:
            joined = str(value).strip()
        self.set_value(name, joined or None)

    @property
    def webhook_secret(self) -> str:
        return self.get_value("webhook_secret")

    def set_webhook_secret(self, value: str | None) -> None:
        self.set_value("webhook_secret", value, is_secret=True)

    @property
    def startup_price_id(self) -> str:
        return self.get_value("startup_price_id")

    @startup_price_id.setter
    def startup_price_id(self, value: str | None) -> None:
        self.set_value("startup_price_id", value)

    @property
    def startup_additional_task_price_id(self) -> str:
        return self.get_value("startup_additional_task_price_id")

    @startup_additional_task_price_id.setter
    def startup_additional_task_price_id(self, value: str | None) -> None:
        self.set_value("startup_additional_task_price_id", value)

    @property
    def startup_task_pack_product_id(self) -> str:
        return self.get_value("startup_task_pack_product_id")

    @startup_task_pack_product_id.setter
    def startup_task_pack_product_id(self, value: str | None) -> None:
        self.set_value("startup_task_pack_product_id", value)

    @property
    def startup_task_pack_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("startup_task_pack_price_ids"))

    @startup_task_pack_price_ids.setter
    def startup_task_pack_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("startup_task_pack_price_ids", value)

    @property
    def startup_contact_cap_product_id(self) -> str:
        return self.get_value("startup_contact_cap_product_id")

    @startup_contact_cap_product_id.setter
    def startup_contact_cap_product_id(self, value: str | None) -> None:
        self.set_value("startup_contact_cap_product_id", value)

    @property
    def startup_contact_cap_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("startup_contact_cap_price_ids"))

    @startup_contact_cap_price_ids.setter
    def startup_contact_cap_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("startup_contact_cap_price_ids", value)

    @property
    def startup_browser_task_limit_product_id(self) -> str:
        return self.get_value("startup_browser_task_limit_product_id")

    @startup_browser_task_limit_product_id.setter
    def startup_browser_task_limit_product_id(self, value: str | None) -> None:
        self.set_value("startup_browser_task_limit_product_id", value)

    @property
    def startup_browser_task_limit_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("startup_browser_task_limit_price_ids"))

    @startup_browser_task_limit_price_ids.setter
    def startup_browser_task_limit_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("startup_browser_task_limit_price_ids", value)

    @property
    def startup_advanced_captcha_resolution_product_id(self) -> str:
        return self.get_value("startup_advanced_captcha_resolution_product_id")

    @startup_advanced_captcha_resolution_product_id.setter
    def startup_advanced_captcha_resolution_product_id(self, value: str | None) -> None:
        self.set_value("startup_advanced_captcha_resolution_product_id", value)

    @property
    def startup_advanced_captcha_resolution_price_id(self) -> str:
        return self.get_value("startup_advanced_captcha_resolution_price_id")

    @startup_advanced_captcha_resolution_price_id.setter
    def startup_advanced_captcha_resolution_price_id(self, value: str | None) -> None:
        self.set_value("startup_advanced_captcha_resolution_price_id", value)

    @property
    def startup_advanced_captcha_resolution_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("startup_advanced_captcha_resolution_price_ids"))

    @startup_advanced_captcha_resolution_price_ids.setter
    def startup_advanced_captcha_resolution_price_ids(
        self, value: list[str] | tuple[str, ...] | str | None
    ) -> None:
        self._set_list_value("startup_advanced_captcha_resolution_price_ids", value)

    @property
    def startup_product_id(self) -> str:
        return self.get_value("startup_product_id")

    @startup_product_id.setter
    def startup_product_id(self, value: str | None) -> None:
        self.set_value("startup_product_id", value)

    @property
    def scale_price_id(self) -> str:
        return self.get_value("scale_price_id")

    @scale_price_id.setter
    def scale_price_id(self, value: str | None) -> None:
        self.set_value("scale_price_id", value)

    @property
    def scale_additional_task_price_id(self) -> str:
        return self.get_value("scale_additional_task_price_id")

    @scale_additional_task_price_id.setter
    def scale_additional_task_price_id(self, value: str | None) -> None:
        self.set_value("scale_additional_task_price_id", value)

    @property
    def scale_task_pack_product_id(self) -> str:
        return self.get_value("scale_task_pack_product_id")

    @scale_task_pack_product_id.setter
    def scale_task_pack_product_id(self, value: str | None) -> None:
        self.set_value("scale_task_pack_product_id", value)

    @property
    def scale_task_pack_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("scale_task_pack_price_ids"))

    @scale_task_pack_price_ids.setter
    def scale_task_pack_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("scale_task_pack_price_ids", value)

    @property
    def scale_contact_cap_product_id(self) -> str:
        return self.get_value("scale_contact_cap_product_id")

    @scale_contact_cap_product_id.setter
    def scale_contact_cap_product_id(self, value: str | None) -> None:
        self.set_value("scale_contact_cap_product_id", value)

    @property
    def scale_contact_cap_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("scale_contact_cap_price_ids"))

    @scale_contact_cap_price_ids.setter
    def scale_contact_cap_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("scale_contact_cap_price_ids", value)

    @property
    def scale_browser_task_limit_product_id(self) -> str:
        return self.get_value("scale_browser_task_limit_product_id")

    @scale_browser_task_limit_product_id.setter
    def scale_browser_task_limit_product_id(self, value: str | None) -> None:
        self.set_value("scale_browser_task_limit_product_id", value)

    @property
    def scale_browser_task_limit_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("scale_browser_task_limit_price_ids"))

    @scale_browser_task_limit_price_ids.setter
    def scale_browser_task_limit_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("scale_browser_task_limit_price_ids", value)

    @property
    def scale_advanced_captcha_resolution_product_id(self) -> str:
        return self.get_value("scale_advanced_captcha_resolution_product_id")

    @scale_advanced_captcha_resolution_product_id.setter
    def scale_advanced_captcha_resolution_product_id(self, value: str | None) -> None:
        self.set_value("scale_advanced_captcha_resolution_product_id", value)

    @property
    def scale_advanced_captcha_resolution_price_id(self) -> str:
        return self.get_value("scale_advanced_captcha_resolution_price_id")

    @scale_advanced_captcha_resolution_price_id.setter
    def scale_advanced_captcha_resolution_price_id(self, value: str | None) -> None:
        self.set_value("scale_advanced_captcha_resolution_price_id", value)

    @property
    def scale_advanced_captcha_resolution_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("scale_advanced_captcha_resolution_price_ids"))

    @scale_advanced_captcha_resolution_price_ids.setter
    def scale_advanced_captcha_resolution_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("scale_advanced_captcha_resolution_price_ids", value)

    @property
    def scale_product_id(self) -> str:
        return self.get_value("scale_product_id")

    @scale_product_id.setter
    def scale_product_id(self, value: str | None) -> None:
        self.set_value("scale_product_id", value)

    @property
    def org_team_product_id(self) -> str:
        return self.get_value("org_team_product_id")

    @org_team_product_id.setter
    def org_team_product_id(self, value: str | None) -> None:
        self.set_value("org_team_product_id", value)

    @property
    def org_team_price_id(self) -> str:
        return self.get_value("org_team_price_id")

    @org_team_price_id.setter
    def org_team_price_id(self, value: str | None) -> None:
        self.set_value("org_team_price_id", value)

    @property
    def org_team_additional_task_price_id(self) -> str:
        return self.get_value("org_team_additional_task_price_id")

    @org_team_additional_task_price_id.setter
    def org_team_additional_task_price_id(self, value: str | None) -> None:
        self.set_value("org_team_additional_task_price_id", value)

    @property
    def org_team_additional_task_product_id(self) -> str:
        return self.get_value("org_team_additional_task_product_id")

    @org_team_additional_task_product_id.setter
    def org_team_additional_task_product_id(self, value: str | None) -> None:
        self.set_value("org_team_additional_task_product_id", value)

    @property
    def org_team_task_pack_product_id(self) -> str:
        return self.get_value("org_team_task_pack_product_id")

    @org_team_task_pack_product_id.setter
    def org_team_task_pack_product_id(self, value: str | None) -> None:
        self.set_value("org_team_task_pack_product_id", value)

    @property
    def org_team_task_pack_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("org_team_task_pack_price_ids"))

    @org_team_task_pack_price_ids.setter
    def org_team_task_pack_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("org_team_task_pack_price_ids", value)

    @property
    def org_team_contact_cap_product_id(self) -> str:
        return self.get_value("org_team_contact_cap_product_id")

    @org_team_contact_cap_product_id.setter
    def org_team_contact_cap_product_id(self, value: str | None) -> None:
        self.set_value("org_team_contact_cap_product_id", value)

    @property
    def org_team_contact_cap_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("org_team_contact_cap_price_ids"))

    @org_team_contact_cap_price_ids.setter
    def org_team_contact_cap_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("org_team_contact_cap_price_ids", value)

    @property
    def org_team_browser_task_limit_product_id(self) -> str:
        return self.get_value("org_team_browser_task_limit_product_id")

    @org_team_browser_task_limit_product_id.setter
    def org_team_browser_task_limit_product_id(self, value: str | None) -> None:
        self.set_value("org_team_browser_task_limit_product_id", value)

    @property
    def org_team_browser_task_limit_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("org_team_browser_task_limit_price_ids"))

    @org_team_browser_task_limit_price_ids.setter
    def org_team_browser_task_limit_price_ids(self, value: list[str] | tuple[str, ...] | str | None) -> None:
        self._set_list_value("org_team_browser_task_limit_price_ids", value)

    @property
    def org_team_advanced_captcha_resolution_product_id(self) -> str:
        return self.get_value("org_team_advanced_captcha_resolution_product_id")

    @org_team_advanced_captcha_resolution_product_id.setter
    def org_team_advanced_captcha_resolution_product_id(self, value: str | None) -> None:
        self.set_value("org_team_advanced_captcha_resolution_product_id", value)

    @property
    def org_team_advanced_captcha_resolution_price_id(self) -> str:
        return self.get_value("org_team_advanced_captcha_resolution_price_id")

    @org_team_advanced_captcha_resolution_price_id.setter
    def org_team_advanced_captcha_resolution_price_id(self, value: str | None) -> None:
        self.set_value("org_team_advanced_captcha_resolution_price_id", value)

    @property
    def org_team_advanced_captcha_resolution_price_ids(self) -> list[str]:
        return self._parse_list_value(self.get_value("org_team_advanced_captcha_resolution_price_ids"))

    @org_team_advanced_captcha_resolution_price_ids.setter
    def org_team_advanced_captcha_resolution_price_ids(
        self, value: list[str] | tuple[str, ...] | str | None
    ) -> None:
        self._set_list_value("org_team_advanced_captcha_resolution_price_ids", value)

    @property
    def startup_dedicated_ip_product_id(self) -> str:
        return self.get_value("startup_dedicated_ip_product_id")

    @startup_dedicated_ip_product_id.setter
    def startup_dedicated_ip_product_id(self, value: str | None) -> None:
        self.set_value("startup_dedicated_ip_product_id", value)

    @property
    def startup_dedicated_ip_price_id(self) -> str:
        return self.get_value("startup_dedicated_ip_price_id")

    @startup_dedicated_ip_price_id.setter
    def startup_dedicated_ip_price_id(self, value: str | None) -> None:
        self.set_value("startup_dedicated_ip_price_id", value)

    @property
    def scale_dedicated_ip_product_id(self) -> str:
        return self.get_value("scale_dedicated_ip_product_id")

    @scale_dedicated_ip_product_id.setter
    def scale_dedicated_ip_product_id(self, value: str | None) -> None:
        self.set_value("scale_dedicated_ip_product_id", value)

    @property
    def scale_dedicated_ip_price_id(self) -> str:
        return self.get_value("scale_dedicated_ip_price_id")

    @scale_dedicated_ip_price_id.setter
    def scale_dedicated_ip_price_id(self, value: str | None) -> None:
        self.set_value("scale_dedicated_ip_price_id", value)

    @property
    def org_team_dedicated_ip_product_id(self) -> str:
        return self.get_value("org_team_dedicated_ip_product_id")

    @org_team_dedicated_ip_product_id.setter
    def org_team_dedicated_ip_product_id(self, value: str | None) -> None:
        self.set_value("org_team_dedicated_ip_product_id", value)

    @property
    def org_team_dedicated_ip_price_id(self) -> str:
        return self.get_value("org_team_dedicated_ip_price_id")

    @org_team_dedicated_ip_price_id.setter
    def org_team_dedicated_ip_price_id(self, value: str | None) -> None:
        self.set_value("org_team_dedicated_ip_price_id", value)

    @property
    def task_meter_id(self) -> str:
        return self.get_value("task_meter_id")

    @task_meter_id.setter
    def task_meter_id(self, value: str | None) -> None:
        self.set_value("task_meter_id", value)

    @property
    def task_meter_event_name(self) -> str:
        return self.get_value("task_meter_event_name")

    @task_meter_event_name.setter
    def task_meter_event_name(self, value: str | None) -> None:
        self.set_value("task_meter_event_name", value)

    @property
    def org_team_task_meter_id(self) -> str:
        return self.get_value("org_team_task_meter_id")

    @org_team_task_meter_id.setter
    def org_team_task_meter_id(self, value: str | None) -> None:
        self.set_value("org_team_task_meter_id", value)

    @property
    def org_team_task_meter_event_name(self) -> str:
        return self.get_value("org_team_task_meter_event_name")

    @org_team_task_meter_event_name.setter
    def org_team_task_meter_event_name(self, value: str | None) -> None:
        self.set_value("org_team_task_meter_event_name", value)

    @property
    def org_task_meter_id(self) -> str:
        return self.get_value("org_task_meter_id")

    @org_task_meter_id.setter
    def org_task_meter_id(self, value: str | None) -> None:
        self.set_value("org_task_meter_id", value)


class StripeConfigEntry(models.Model):
    """Individual Stripe configuration value scoped to an environment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    config = models.ForeignKey(
        StripeConfig,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    name = models.CharField(max_length=128)
    is_secret = models.BooleanField(default=False)
    value_text = models.TextField(blank=True, default="")
    value_encrypted = models.BinaryField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("config", "name")
        indexes = [
            models.Index(fields=["config", "name"]),
        ]

    def __str__(self) -> str:
        return f"StripeConfigEntry<{self.config.release_env}:{self.name}>"

    @staticmethod
    def _decrypt(value: bytes | None) -> str:
        if not value:
            return ""
        from .encryption import SecretsEncryption

        return SecretsEncryption.decrypt_value(value)

    @staticmethod
    def _encrypt(value: str | None) -> bytes | None:
        if not value:
            return None
        from .encryption import SecretsEncryption

        return SecretsEncryption.encrypt_value(value)

    def get_value(self) -> str:
        if self.is_secret:
            return self._decrypt(self.value_encrypted)
        return self.value_text or ""

    def set_value(self, value: str | None) -> None:
        if self.is_secret:
            self.value_encrypted = self._encrypt(value)
            self.value_text = ""
        else:
            self.value_text = value or ""
            self.value_encrypted = None

    @property
    def has_value(self) -> bool:
        if self.is_secret:
            return bool(self.value_encrypted)
        return bool(self.value_text)


class SystemSetting(models.Model):
    """System-level configuration override stored in the database."""

    key = models.CharField(max_length=128, unique=True)
    value_text = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        verbose_name = "System setting"
        verbose_name_plural = "System settings"

    def __str__(self) -> str:
        return f"SystemSetting<{self.key}>"

    @property
    def has_value(self) -> bool:
        return bool(self.value_text)

    def clean(self) -> None:
        super().clean()
        from api.services import system_settings

        if self.key not in system_settings.LOGIN_TOGGLE_KEYS:
            return

        definition = system_settings.get_setting_definition(self.key)
        if definition is None:
            return

        value_text = (self.value_text or "").strip()
        if value_text:
            try:
                coerced = definition.coerce(value_text)
            except ValueError as exc:
                raise ValidationError({"value_text": str(exc)})
            clear = False
        else:
            coerced = None
            clear = True

        try:
            system_settings.validate_login_toggle_update(self.key, coerced, clear=clear)
        except ValueError as exc:
            raise ValidationError({"value_text": str(exc)})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class MeteringBatch(models.Model):
    """Audit record linking a batch of reserved usage to a Stripe meter event.

    Each batch corresponds to a unique meter_batch_key reserved on usage rows.
    We also persist the idempotency key used with Stripe for exactly-once semantics.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="metering_batches",
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        'Organization',
        on_delete=models.CASCADE,
        related_name='metering_batches',
        null=True,
        blank=True,
    )
    batch_key = models.CharField(max_length=64, unique=True, db_index=True)
    idempotency_key = models.CharField(max_length=128, unique=True, db_index=True)
    period_start = models.DateField()
    period_end = models.DateField()
    total_credits = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    rounded_quantity = models.IntegerField(default=0)
    stripe_event_id = models.CharField(max_length=128, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"], name="meter_batch_user_ts_idx"),
            models.Index(fields=["organization", "created_at"], name="meter_batch_org_ts_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                name="metering_batch_owner_xor",
                check=(
                    (
                        models.Q(user__isnull=False, organization__isnull=True)
                    ) | (
                        models.Q(user__isnull=True, organization__isnull=False)
                    )
                ),
            )
        ]

    def __str__(self) -> str:
        owner = self.user_id or self.organization_id
        return f"MeteringBatch({self.batch_key}) owner={owner} qty={self.rounded_quantity}"

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

class PublicProfile(models.Model):
    """Public profile handle for sharing templates."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="public_profile",
    )
    handle = models.SlugField(
        max_length=32,
        unique=True,
        help_text="Public profile handle (lowercase letters, numbers, and hyphens).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["handle"]

    def clean(self):
        super().clean()
        from api.public_profiles import validate_public_handle

        self.handle = validate_public_handle(self.handle)

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"PublicProfile<{self.handle}>"


class PersistentAgentTemplate(models.Model):
    """Curated template for pre-configured always-on pretrained workers."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Internal identifier for referencing this template in code and analytics.",
    )
    public_profile = models.ForeignKey(
        PublicProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="templates",
        help_text="Public profile that owns this template when shared publicly.",
    )
    slug = models.SlugField(
        max_length=80,
        blank=True,
        help_text="Public-facing slug used in template URLs.",
    )
    source_agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_templates",
        help_text="Agent this template was cloned from, when applicable.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_templates",
        help_text="User who created this template, if applicable.",
    )
    display_name = models.CharField(max_length=255)
    tagline = models.CharField(max_length=255)
    description = models.TextField()
    charter = models.TextField(help_text="Pre-built charter the agent will start with.")
    base_schedule = models.CharField(
        max_length=128,
        blank=True,
        help_text="Cron-like schedule expression or interval guideline (e.g., '@daily').",
    )
    schedule_jitter_minutes = models.PositiveIntegerField(
        default=0,
        help_text="Maximum minutes of jitter to apply to the base schedule when instancing.",
    )
    event_triggers = models.JSONField(
        default=list,
        blank=True,
        help_text="List of event trigger definitions (webhook names, keywords, etc.).",
    )
    default_tools = models.JSONField(
        default=list,
        blank=True,
        help_text="MCP tool identifiers to enable automatically when hired.",
    )
    recommended_contact_channel = models.CharField(
        max_length=16,
        blank=True,
        help_text="Default contact preference (e.g., 'email', 'sms').",
    )
    category = models.CharField(
        max_length=64,
        blank=True,
        help_text="Grouping label used for UI filtering (e.g., 'Research', 'Operations').",
    )
    hero_image_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional static asset path used for UI illustration.",
    )
    priority = models.PositiveIntegerField(
        default=100,
        help_text="Lower numbers appear first in the directory UI.",
    )
    is_active = models.BooleanField(default=True)
    show_on_homepage = models.BooleanField(
        default=False,
        help_text="Whether to feature this template on the home page.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "display_name"]
        constraints = [
            UniqueConstraint(
                fields=["public_profile", "slug"],
                condition=Q(public_profile__isnull=False),
                name="unique_public_profile_template_slug",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple repr
        return f"PretrainedWorkerTemplate<{self.display_name}>"


class ToolFriendlyName(models.Model):
    """Human-friendly labels for tool identifiers surfaced in templates."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tool_name = models.CharField(
        max_length=128,
        unique=True,
        help_text="Internal tool identifier (e.g., 'google_sheets-add-single-row').",
    )
    display_name = models.CharField(
        max_length=255,
        help_text="User-facing label shown in the directory UI.",
    )
    description = models.TextField(
        blank=True,
        help_text="Optional notes to help admins remember what the tool does.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tool_name"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.display_name


class PersistentAgent(models.Model):
    """
    A persistent agent that runs automatically on a schedule.
    """
    objects = PersistentAgentQuerySet.as_manager()
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
    charter = models.TextField(blank=True)
    short_description = models.CharField(
        max_length=280,
        blank=True,
        help_text="Generated short summary of the agent charter for listings.",
    )
    short_description_charter_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 of the charter used to generate short_description.",
    )
    short_description_requested_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 of the charter currently pending short description generation.",
    )
    avatar = models.FileField(
        upload_to="agent_avatars/%Y/%m/%d/",
        blank=True,
        null=True,
        help_text="Optional avatar image displayed for this agent.",
    )
    mini_description = models.CharField(
        max_length=80,
        blank=True,
        help_text="Generated ultra-short summary of the agent charter for compact displays.",
    )
    mini_description_charter_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 of the charter used to generate mini_description.",
    )
    mini_description_requested_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 of the charter currently pending mini description generation.",
    )
    preferred_llm_tier = models.ForeignKey(
        IntelligenceTier,
        on_delete=models.PROTECT,
        related_name="preferred_by_agents",
        default=_get_default_intelligence_tier_id,
        help_text="Preferred intelligence tier controlling LLM routing for this agent.",
    )
    agent_color = models.ForeignKey(
        AgentColor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="persistent_agents",
        help_text="UI accent color assigned to this agent.",
    )
    tags = models.JSONField(
        default=list,
        blank=True,
        help_text="List of descriptive tags generated from the charter to aid discovery.",
    )
    tags_charter_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 of the charter used to generate tags.",
    )
    tags_requested_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 of the charter currently pending tag generation.",
    )
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

    @property
    def preferred_proxy(self):
        """Return the proxy selected on the backing browser agent, if any."""
        try:
            return self.browser_use_agent.preferred_proxy
        except (BrowserUseAgent.DoesNotExist, AttributeError):
            return None

    @property
    def preferred_proxy_id(self):
        proxy = self.preferred_proxy
        return getattr(proxy, "id", None)

    is_active = models.BooleanField(default=True, help_text="Whether this agent is currently active")
    daily_credit_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Soft daily credit target; system enforces a hard stop at 2× this value. Null means unlimited.",
    )
    daily_credit_hard_limit_notice_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time the owner was notified that the daily hard limit was reached.",
    )
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
    sent_expiration_email = models.BooleanField(
        default=False,
        help_text="Whether a soft-expiration notification has been sent for the current inactivity period.",
    )

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
    proactive_opt_in = models.BooleanField(
        default=True,
        help_text="Enable Gobii to proactively start conversations offering related help for this agent.",
    )
    proactive_last_trigger_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent proactive outreach trigger.",
    )
    # NOTE: Enabled MCP tools are now tracked in PersistentAgentEnabledTool.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['schedule'], name='pa_schedule_idx'),
            models.Index(fields=['life_state', 'is_active'], name='pa_life_active_idx'),
            models.Index(fields=['last_interaction_at'], name='pa_last_interact_idx'),
            models.Index(fields=['proactive_last_trigger_at'], name='pa_proactive_last_idx'),
            models.Index(
                fields=[
                    'proactive_opt_in',
                    'is_active',
                    'life_state',
                    'proactive_last_trigger_at',
                    'last_interaction_at',
                    'created_at',
                ],
                name='pa_proactive_sched_idx',
            ),
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
        if self.organization_id:
            self._validate_org_seats()
        if self.schedule:
            try:
                # Use the same parser that's used for task scheduling to ensure consistency.
                from api.agent.core.schedule_parser import ScheduleParser
                ScheduleParser.parse(self.schedule)
            except ValueError as e:
                raise ValidationError({'schedule': str(e)})
        tags = getattr(self, "tags", None) or []
        if not isinstance(tags, list):
            raise ValidationError({"tags": "Tags must be provided as a list of strings."})
        if len(tags) > 5:
            raise ValidationError({"tags": "At most 5 tags may be assigned to an agent."})
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip():
                raise ValidationError({"tags": "Each tag must be a non-empty string."})
            if len(tag.strip()) > 64:
                raise ValidationError({"tags": "Tags must be 64 characters or fewer."})

    def assign_agent_color(self, *, force: bool = False) -> None:
        """Assign a color, preferring unused palette entries for this owner."""
        if self.agent_color_id and not force:
            return
        # Skip assignment when the colors table has not been created yet (e.g., historical migrations).
        try:
            table_names = connection.introspection.table_names()
        except (ProgrammingError, OperationalError):
            return
        if AgentColor._meta.db_table not in table_names:
            return
        organization_ref = getattr(self, "organization", None)
        if organization_ref is None:
            organization_ref = self.organization_id
        color = AgentColor.pick_for_owner(user=self.user, organization=organization_ref)
        if color is None:
            raise ValidationError({
                "agent_color": (
                    "No available agent colors remain for this owner. "
                    "Add more agent colors before creating additional agents."
                )
            })
        self.agent_color = color

    def get_display_color(self) -> str:
        """Return the hex color used to render the agent in the UI."""
        if self.agent_color_id:
            cache = getattr(self._state, "fields_cache", {})
            cached_color = cache.get("agent_color")
            if cached_color:
                return cached_color.hex_value
            try:
                return self.agent_color.hex_value  # type: ignore[union-attr]
            except AgentColor.DoesNotExist:
                pass
        return AgentColor.get_default_hex()

    def get_avatar_url(self) -> str | None:
        """Return a usable URL for the agent avatar, if set."""
        file_field = getattr(self, "avatar", None)
        if not file_field or not getattr(file_field, "name", None) or not self.pk:
            return None
        try:
            from django.urls import reverse, NoReverseMatch
            return reverse("agent_avatar", kwargs={"pk": self.pk})
        except NoReverseMatch:
            try:
                return file_field.url
            except ValueError:
                return None

    def _validate_org_seats(self):
        billing = getattr(self.organization, "billing", None)
        if not billing or billing.purchased_seats <= 0:
            raise ValidationError({
                "organization": "Purchase organization seats before creating org-owned agents."
            })

    def __str__(self):
        schedule_display = self.schedule if self.schedule else "No schedule"
        return f"PersistentAgent: {self.name} (Schedule: {schedule_display})"

    def get_daily_credit_soft_target(self) -> Decimal | None:
        """Return the configured soft daily credit target, or None if unlimited."""
        limit = self.daily_credit_limit
        if limit is None:
            return None
        limit_value = limit if isinstance(limit, Decimal) else Decimal(limit)
        if limit_value == Decimal("0"):
            return None
        return limit_value

    def get_daily_credit_hard_limit(self) -> Decimal | None:
        """Return the derived hard limit (2× soft target) or None for unlimited agents."""
        from api.services.daily_credit_settings import get_daily_credit_settings_for_owner

        soft_target = self.get_daily_credit_soft_target()
        if soft_target is None:
            return None
        owner = self.organization or self.user
        credit_settings = get_daily_credit_settings_for_owner(owner)
        multiplier = credit_settings.hard_limit_multiplier
        try:
            multiplier = Decimal(multiplier)
        except Exception:
            multiplier = Decimal("2")
        return (soft_target * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def get_daily_credit_usage(self, usage_date=None) -> Decimal:
        """Return the credits consumed by this agent on the given date."""
        usage_date = usage_date or timezone.localdate()
        start = datetime.datetime.combine(usage_date, datetime.time.min)
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
        end = start + datetime.timedelta(days=1)

        total = (
            self.steps.filter(
                created_at__gte=start,
                created_at__lt=end,
                credits_cost__isnull=False,
            ).aggregate(sum=Sum("credits_cost"))
        ).get("sum")

        return total if total is not None else Decimal("0")

    def get_daily_credit_soft_target_remaining(self, usage_date=None) -> Decimal | None:
        """Return remaining credits before the soft target is exceeded."""
        soft_target = self.get_daily_credit_soft_target()
        if soft_target is None:
            return None
        used = self.get_daily_credit_usage(usage_date=usage_date)
        remaining = soft_target - used
        if remaining <= Decimal("0"):
            return Decimal("0")
        return remaining

    def get_daily_credit_remaining(self, usage_date=None) -> Decimal | None:
        """Return remaining credits before the derived hard limit is enforced."""
        limit = self.get_daily_credit_hard_limit()
        if limit is None:
            return None
        used = self.get_daily_credit_usage(usage_date=usage_date)
        remaining = limit - used
        return remaining if remaining > Decimal("0") else Decimal("0")

    @tracer.start_as_current_span("WHITELIST PersistentAgent Inbound Sender Check")
    def is_sender_whitelisted(self, channel: CommsChannel | str, address: str) -> bool:
        """Check if an inbound address/number is allowed to contact this agent."""
        channel_val = channel.value if isinstance(channel, CommsChannel) else str(channel)
        addr = (address or "").strip()
        addr_lower = addr.lower()

        logger.info("Whitelist check for channel: %s, address: %s, policy=%s", channel_val, addr_lower, self.whitelist_policy)

        if channel_val == CommsChannel.WEB:
            return self._is_allowed_web_address(addr, direction="inbound")

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

        if channel_val == CommsChannel.WEB:
            return self._is_allowed_web_address(addr, direction="outbound")

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

            if AgentCollaborator.objects.filter(
                agent=self,
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
        elif channel_val == CommsChannel.WEB:
            # Owner is always allowed
            user_id, agent_id = parse_web_user_address(addr)
            if agent_id == str(self.id) and user_id == self.user_id:
                return True

            if self.organization_id and agent_id == str(self.id):
                from .models import OrganizationMembership

                if OrganizationMembership.objects.filter(
                    org=self.organization,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                    user_id=user_id,
                ).exists():
                    return True

            if AgentCollaborator.objects.filter(agent=self, user_id=user_id).exists():
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

    def _is_allowed_web_address(self, address: str, direction: str = "both") -> bool:
        """Return True if a web chat address is permitted for the requested direction."""
        addr = (address or "").strip()
        user_id, agent_id = parse_web_user_address(addr)

        if agent_id != str(self.id) or user_id is None:
            return False

        # Owner is always allowed regardless of policy
        if user_id == self.user_id:
            return True

        # Organization members are implicitly allowed
        if self.organization_id:
            from .models import OrganizationMembership

            if OrganizationMembership.objects.filter(
                org=self.organization,
                status=OrganizationMembership.OrgStatus.ACTIVE,
                user_id=user_id,
            ).exists():
                return True

        if AgentCollaborator.objects.filter(agent=self, user_id=user_id).exists():
            return True

        # Manual allowlist entries can extend access beyond owner/org members
        if self.whitelist_policy == self.WhitelistPolicy.MANUAL:
            try:
                query = CommsAllowlistEntry.objects.filter(
                    agent=self,
                    channel=CommsChannel.WEB,
                    address=addr,
                    is_active=True,
                )

                if direction == "inbound":
                    query = query.filter(allow_inbound=True)
                elif direction == "outbound":
                    query = query.filter(allow_outbound=True)
                else:
                    query = query.filter(
                        models.Q(allow_inbound=True) | models.Q(allow_outbound=True)
                    )

                if query.exists():
                    return True
            except Exception as exc:  # pragma: no cover - safety logging
                logger.error(
                    "Error checking web allowlist for agent %s: %s", self.id, exc, exc_info=True
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
            if AgentCollaborator.objects.filter(
                agent=self,
                user__email__iexact=email_only,
            ).exists():
                return True
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

        if channel_val == CommsChannel.WEB:
            user_id, agent_id = parse_web_user_address(addr_raw)
            if agent_id != str(self.id) or user_id is None:
                return False
            if self.organization_id:
                from .models import OrganizationMembership

                return OrganizationMembership.objects.filter(
                    org=self.organization,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                    user_id=user_id,
                ).exists()
            return user_id == self.user_id

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

        # Track whether we should reset the sent_expiration_email flag when the agent wakes up.
        reset_sent_flag = False
        update_fields = kwargs.get("update_fields")
        if self.agent_color_id is None:
            self.assign_agent_color()
            if update_fields is not None and "agent_color" not in update_fields:
                update_fields = list(update_fields)
                update_fields.append("agent_color")
                kwargs["update_fields"] = update_fields

        # For updates, we need to check if schedule-related fields have changed.
        sync_needed = False
        shutdown_reasons: list[str] = []
        if not is_new:
            try:
                # Fetch the current state from the database before it's saved.
                old_instance = PersistentAgent.objects.get(pk=self.pk)
                if (old_instance.schedule != self.schedule or
                    old_instance.is_active != self.is_active):
                    sync_needed = True

                consider_last_interaction = (
                    update_fields is None or "last_interaction_at" in update_fields
                )
                if consider_last_interaction and old_instance.last_interaction_at != self.last_interaction_at:
                    reset_sent_flag = old_instance.sent_expiration_email or self.sent_expiration_email

                # Detect shutdown‑adjacent transitions to trigger centralized cleanup
                try:
                    # is_active: True -> False (manual pause)
                    if old_instance.is_active and not self.is_active:
                        shutdown_reasons.append("PAUSE")
                    # schedule: non‑empty -> empty/None (cron disabled)
                    def _truthy_sched(val: str | None) -> bool:
                        try:
                            return bool((val or "").strip())
                        except Exception:
                            return bool(val)
                    old_sched_truthy = _truthy_sched(getattr(old_instance, "schedule", None))
                    new_sched_truthy = _truthy_sched(getattr(self, "schedule", None))
                    # Trigger when schedule transitions to disabled; be lenient to ensure cleanup fires
                    if old_sched_truthy and not new_sched_truthy:
                        # Only append once when transitioning from scheduled -> disabled
                        shutdown_reasons.append("CRON_DISABLED")
                    # life_state: ACTIVE -> EXPIRED (soft expire)
                    if (
                        getattr(old_instance, "life_state", None) == self.LifeState.ACTIVE
                        and getattr(self, "life_state", None) == self.LifeState.EXPIRED
                    ):
                        shutdown_reasons.append("SOFT_EXPIRE")
                except Exception:
                    # Defensive: do not block save on detection errors
                    logger.exception("Failed to compute shutdown reasons for agent %s", self.id)
            except PersistentAgent.DoesNotExist:
                # If it doesn't exist in the DB yet, treat it as a new instance.
                is_new = True

        if reset_sent_flag:
            self.sent_expiration_email = False
            if update_fields is not None and "sent_expiration_email" not in update_fields:
                update_fields_list = list(update_fields)
                update_fields_list.append("sent_expiration_email")
                kwargs["update_fields"] = update_fields_list
                update_fields = update_fields_list

        super().save(*args, **kwargs)

        # If it's a new instance or a relevant field changed, schedule the
        # Redis side-effect to run only after a successful DB commit.
        if is_new or sync_needed:
            transaction.on_commit(self._sync_celery_beat_task)

        # If any shutdown reasons were detected, enqueue centralized cleanup
        if shutdown_reasons:
            def _enqueue_cleanup():
                try:
                    from api.services.agent_lifecycle import AgentLifecycleService, AgentShutdownReason

                    # Map raw strings to constants (same values) for readability
                    reason_map = {
                        "PAUSE": AgentShutdownReason.PAUSE,
                        "CRON_DISABLED": AgentShutdownReason.CRON_DISABLED,
                        "SOFT_EXPIRE": AgentShutdownReason.SOFT_EXPIRE,
                    }
                    for r in shutdown_reasons:
                        AgentLifecycleService.shutdown(str(self.id), reason_map.get(r, r), meta={
                            "source": "model.save",
                        })
                except Exception:
                    logger.exception("Failed to enqueue agent cleanup for %s", self.id)

            transaction.on_commit(_enqueue_cleanup)

    def delete(self, *args, **kwargs):
        browser_agent_id = getattr(self, "browser_use_agent_id", None)
        if browser_agent_id:
            browser_agent_exists = BrowserUseAgent.objects.filter(pk=browser_agent_id).exists()
            if not browser_agent_exists:
                logger.warning(
                    "PersistentAgent %s is missing BrowserUseAgent %s; proceeding with orphan cleanup during delete",
                    self.id,
                    browser_agent_id,
                )
                # Clear any cached relation so Django's collector doesn't try to load it.
                self.browser_use_agent_id = None
                if hasattr(self, "_prefetched_objects_cache"):
                    self._prefetched_objects_cache.pop("browser_use_agent", None)
                self.__dict__.pop("browser_use_agent", None)
        # Schedule the removal of the Celery Beat task to happen only after
        # the database transaction that deletes this instance successfully commits.
        transaction.on_commit(self._remove_celery_beat_task)
        # Also enqueue centralized cleanup as a HARD_DELETE reason
        try:
            from api.services.agent_lifecycle import AgentLifecycleService, AgentShutdownReason
            agent_id = self.id

            transaction.on_commit(lambda: AgentLifecycleService.shutdown(str(agent_id), AgentShutdownReason.HARD_DELETE, meta={
                "source": "model.delete",
            }))
        except Exception:
            logger.exception("Failed to schedule agent HARD_DELETE cleanup for %s", self.id)

        try:
            return super().delete(*args, **kwargs)
        except BrowserUseAgent.DoesNotExist:
            logger.warning(
                "PersistentAgent %s triggered BrowserUseAgent.DoesNotExist during delete; retrying with queryset delete",
                self.id,
                exc_info=True,
            )
            return self.__class__.objects.filter(pk=self.pk).delete()


class PersistentAgentKanbanCard(models.Model):
    """Kanban card assigned to a persistent agent."""

    class Status(models.TextChoices):
        TODO = "todo", "To Do"
        DOING = "doing", "Doing"
        DONE = "done", "Done"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assigned_agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="kanban_cards",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.TODO,
    )
    priority = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-priority", "created_at"]
        indexes = [
            models.Index(
                fields=["assigned_agent", "status", "-priority"],
                name="kanban_agent_status_pri_idx",
            ),
            models.Index(
                fields=["assigned_agent", "status", "-completed_at"],
                name="kanban_agent_status_done_idx",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple display helper
        return f"Kanban<{self.title}> ({self.status})"


class PersistentAgentKanbanEvent(models.Model):
    """Persisted kanban timeline event for chat rehydration."""

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"
        UPDATED = "updated", "Updated"
        DELETED = "deleted", "Deleted"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="kanban_events",
    )
    cursor_value = models.BigIntegerField()
    cursor_identifier = models.UUIDField(unique=True)
    display_text = models.TextField()
    primary_action = models.CharField(max_length=16, choices=Action.choices)
    todo_count = models.PositiveIntegerField(default=0)
    doing_count = models.PositiveIntegerField(default=0)
    done_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cursor_value", "-cursor_identifier"]
        indexes = [
            models.Index(fields=["agent", "-cursor_value"], name="kanban_event_agent_recent_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple display helper
        return f"KanbanEvent<{self.agent_id}> ({self.primary_action})"


class PersistentAgentKanbanEventTitle(models.Model):
    """Snapshot titles stored alongside a kanban event."""

    class Status(models.TextChoices):
        TODO = "todo", "To Do"
        DOING = "doing", "Doing"
        DONE = "done", "Done"

    event = models.ForeignKey(
        PersistentAgentKanbanEvent,
        on_delete=models.CASCADE,
        related_name="titles",
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    position = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=255)

    class Meta:
        ordering = ["status", "position"]
        indexes = [
            models.Index(fields=["event", "status", "position"], name="kanban_event_title_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple display helper
        return f"KanbanEventTitle<{self.status}:{self.position}>"


class PersistentAgentKanbanEventChange(models.Model):
    """Stored kanban change metadata for a timeline event."""

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"
        UPDATED = "updated", "Updated"
        DELETED = "deleted", "Deleted"
        ARCHIVED = "archived", "Archived"

    event = models.ForeignKey(
        PersistentAgentKanbanEvent,
        on_delete=models.CASCADE,
        related_name="changes",
    )
    card_id = models.UUIDField()
    title = models.CharField(max_length=255)
    action = models.CharField(max_length=16, choices=Action.choices)
    from_status = models.CharField(
        max_length=16,
        choices=PersistentAgentKanbanCard.Status.choices,
        null=True,
        blank=True,
    )
    to_status = models.CharField(
        max_length=16,
        choices=PersistentAgentKanbanCard.Status.choices,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["event"], name="kanban_event_change_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple display helper
        return f"KanbanEventChange<{self.action}:{self.card_id}>"


class MCPServerConfig(models.Model):
    """Configurable MCP server definition scoped to platform, org, or user."""

    RESERVED_PLATFORM_NAMES = {"pipedream"}

    class Scope(models.TextChoices):
        PLATFORM = "platform", "Platform"
        ORGANIZATION = "organization", "Organization"
        USER = "user", "User"

    class AuthMethod(models.TextChoices):
        NONE = "none", "None"
        BEARER_TOKEN = "bearer_token", "Bearer Token"
        OAUTH2 = "oauth2", "OAuth 2.0"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=32, choices=Scope.choices)
    organization = models.ForeignKey(
        "Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mcp_server_configs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mcp_server_configs",
    )
    name = models.SlugField(max_length=64)
    display_name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    command = models.CharField(max_length=255, blank=True)
    command_args = models.JSONField(default=list, blank=True)
    url = models.CharField(max_length=512, blank=True)
    auth_method = models.CharField(
        max_length=32,
        choices=AuthMethod.choices,
        default=AuthMethod.NONE,
    )
    prefetch_apps = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    env_json_encrypted = models.BinaryField(null=True, blank=True)
    headers_json_encrypted = models.BinaryField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["scope", "is_active"], name="mcp_server_scope_active_idx"),
            models.Index(fields=["organization", "name"], name="mcp_server_org_name_idx"),
            models.Index(fields=["user", "name"], name="mcp_server_user_name_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "name"],
                name="unique_platform_mcp_server_name",
                condition=Q(scope="platform"),
            ),
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_org_mcp_server_name",
                condition=Q(scope="organization"),
            ),
            models.UniqueConstraint(
                fields=["user", "name"],
                name="unique_user_mcp_server_name",
                condition=Q(scope="user"),
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        owner = self.organization or self.user or "platform"
        return f"MCPServerConfig<{self.name} scope={self.scope} owner={owner}>"

    def clean(self):
        """Validate scope ownership and transport requirements."""
        super().clean()
        if self.scope == self.Scope.PLATFORM:
            if self.organization_id or self.user_id:
                raise ValidationError("Platform-scoped MCP servers cannot be linked to users or organizations")
        elif self.scope == self.Scope.ORGANIZATION:
            if not self.organization_id or self.user_id:
                raise ValidationError("Organization-scoped MCP servers must reference an organization only")
        elif self.scope == self.Scope.USER:
            if not self.user_id or self.organization_id:
                raise ValidationError("User-scoped MCP servers must reference a user only")
        else:
            raise ValidationError({"scope": "Invalid MCP server scope"})

        if not self.command and not self.url:
            raise ValidationError("MCP servers require either a command or a URL")

        reserved = {name.lower() for name in self.RESERVED_PLATFORM_NAMES}
        if self.scope != self.Scope.PLATFORM and self.name and self.name.lower() in reserved:
            raise ValidationError({"name": "This identifier is reserved for platform-managed integrations."})

    # Secret-backed fields -------------------------------------------------
    def _decrypt_json(self, payload: bytes | None) -> dict[str, str]:
        if not payload:
            return {}
        try:
            from .encryption import SecretsEncryption

            raw = SecretsEncryption.decrypt_value(payload)
            return json.loads(raw)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to decrypt MCP server secrets %s: %s", self.id, exc)
            return {}

    def _encrypt_json(self, value: dict[str, str] | None) -> bytes | None:
        if not value:
            return None
        try:
            from .encryption import SecretsEncryption

            return SecretsEncryption.encrypt_value(json.dumps(value))
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to encrypt MCP server secrets %s: %s", self.id or "<new>", exc)
            raise

    @property
    def environment(self) -> dict[str, str]:
        return self._decrypt_json(self.env_json_encrypted)

    @environment.setter
    def environment(self, value: dict[str, str] | None) -> None:
        self.env_json_encrypted = self._encrypt_json(value)

    @property
    def headers(self) -> dict[str, str]:
        return self._decrypt_json(self.headers_json_encrypted)

    @headers.setter
    def headers(self, value: dict[str, str] | None) -> None:
        self.headers_json_encrypted = self._encrypt_json(value)


class PersistentAgentSystemMessageBroadcast(models.Model):
    """Represents a single broadcast directive duplicated for all agents."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    body = models.TextField(help_text="Directive text sent to all persistent agents.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_agent_system_broadcasts",
        help_text="Admin user that initiated this broadcast.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        if self.created_at is None:
            return "Broadcast (unsaved)"
        return f"Broadcast at {self.created_at:%Y-%m-%d %H:%M:%S}"


class PersistentAgentSystemMessage(models.Model):
    """
    High-priority system directives injected into an agent's system prompt.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="system_prompt_messages",
        help_text="Agent that should receive this system directive.",
    )
    body = models.TextField(help_text="System directive text injected ahead of the agent's instructions.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_agent_system_messages",
        help_text="Admin user that issued this directive.",
    )
    broadcast = models.ForeignKey(
        PersistentAgentSystemMessageBroadcast,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="system_messages",
        help_text="Broadcast that created this directive, if applicable.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when this directive was injected into the system prompt.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Disable to keep the record but skip injecting it into future prompts.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "delivered" if self.delivered_at else "pending"
        return f"System message for {self.agent_id} ({status})"


class PersistentAgentMCPServer(models.Model):
    """Explicit mapping for personal MCP servers enabled on an agent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="personal_mcp_servers",
    )
    server_config = models.ForeignKey(
        MCPServerConfig,
        on_delete=models.CASCADE,
        related_name="agent_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "server_config"],
                name="unique_agent_personal_server",
            )
        ]
        indexes = [
            models.Index(fields=["agent", "server_config"], name="agent_personal_server_idx"),
        ]


class MCPServerOAuthCredential(models.Model):
    """Encrypted OAuth credential store for MCP servers."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    server_config = models.OneToOneField(
        MCPServerConfig,
        on_delete=models.CASCADE,
        related_name="oauth_credential",
    )
    organization = models.ForeignKey(
        "Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mcp_oauth_credentials",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mcp_oauth_credentials",
    )
    client_id = models.CharField(max_length=256, blank=True)
    client_secret_encrypted = models.BinaryField(null=True, blank=True)
    access_token_encrypted = models.BinaryField(null=True, blank=True)
    refresh_token_encrypted = models.BinaryField(null=True, blank=True)
    id_token_encrypted = models.BinaryField(null=True, blank=True)
    token_type = models.CharField(max_length=32, blank=True)
    scope = models.CharField(max_length=512, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization"], name="mcp_oauth_credential_org_idx"),
            models.Index(fields=["user"], name="mcp_oauth_credential_user_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"MCPServerOAuthCredential<{self.server_config_id}>"

    # Encrypted field helpers -------------------------------------------------
    @staticmethod
    def _encrypt_text(value: str | None) -> bytes | None:
        if not value:
            return None
        from .encryption import SecretsEncryption

        return SecretsEncryption.encrypt_value(value)

    @staticmethod
    def _decrypt_text(payload: bytes | None) -> str:
        if not payload:
            return ""
        try:
            from .encryption import SecretsEncryption

            return SecretsEncryption.decrypt_value(payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to decrypt MCP OAuth credential payload")
            return ""

    @property
    def client_secret(self) -> str:
        return self._decrypt_text(self.client_secret_encrypted)

    @client_secret.setter
    def client_secret(self, value: str | None) -> None:
        self.client_secret_encrypted = self._encrypt_text(value)

    @property
    def access_token(self) -> str:
        return self._decrypt_text(self.access_token_encrypted)

    @access_token.setter
    def access_token(self, value: str | None) -> None:
        self.access_token_encrypted = self._encrypt_text(value)

    @property
    def refresh_token(self) -> str:
        return self._decrypt_text(self.refresh_token_encrypted)

    @refresh_token.setter
    def refresh_token(self, value: str | None) -> None:
        self.refresh_token_encrypted = self._encrypt_text(value)

    @property
    def id_token(self) -> str:
        return self._decrypt_text(self.id_token_encrypted)

    @id_token.setter
    def id_token(self, value: str | None) -> None:
        self.id_token_encrypted = self._encrypt_text(value)


class MCPServerOAuthSession(models.Model):
    """Ephemeral OAuth session state for MCP authentication flows."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    server_config = models.ForeignKey(
        MCPServerConfig,
        on_delete=models.CASCADE,
        related_name="oauth_sessions",
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_oauth_sessions",
    )
    organization = models.ForeignKey(
        "Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mcp_oauth_sessions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mcp_oauth_user_sessions",
    )
    state = models.CharField(max_length=255, unique=True)
    redirect_uri = models.CharField(max_length=512, blank=True)
    scope = models.CharField(max_length=512, blank=True)
    code_challenge = models.CharField(max_length=255, blank=True)
    code_challenge_method = models.CharField(max_length=32, blank=True)
    code_verifier_encrypted = models.BinaryField(null=True, blank=True)
    token_endpoint = models.CharField(max_length=512, blank=True)
    client_id = models.CharField(max_length=256, blank=True)
    client_secret_encrypted = models.BinaryField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"], name="mcp_oauth_session_expiry_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"MCPServerOAuthSession<{self.server_config_id} state={self.state}>"

    @staticmethod
    def _encrypt_text(value: str | None) -> bytes | None:
        if not value:
            return None
        from .encryption import SecretsEncryption

        return SecretsEncryption.encrypt_value(value)

    @staticmethod
    def _decrypt_text(payload: bytes | None) -> str:
        if not payload:
            return ""
        try:
            from .encryption import SecretsEncryption

            return SecretsEncryption.decrypt_value(payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to decrypt MCP OAuth session payload")
            return ""

    @property
    def code_verifier(self) -> str:
        return self._decrypt_text(self.code_verifier_encrypted)

    @code_verifier.setter
    def code_verifier(self, value: str | None) -> None:
        self.code_verifier_encrypted = self._encrypt_text(value)

    @property
    def client_secret(self) -> str:
        return self._decrypt_text(self.client_secret_encrypted)

    @client_secret.setter
    def client_secret(self, value: str | None) -> None:
        self.client_secret_encrypted = self._encrypt_text(value)

    def has_expired(self) -> bool:
        from django.utils import timezone

        return timezone.now() >= self.expires_at

    def clean(self):
        super().clean()
        if self.server_config.scope != MCPServerConfig.Scope.USER:
            raise ValidationError("Only user-scoped MCP servers can be manually assigned to agents")


class PersistentAgentEnabledTool(models.Model):
    """Normalized record of a tool enabled for a persistent agent.

    Replaces the old JSON fields on PersistentAgent:
    - enabled_mcp_tools (list[str])
    - mcp_tool_usage (dict[str -> last_used_epoch_seconds])
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="enabled_tools",
    )
    tool_full_name = models.CharField(max_length=256)
    # Optional denormalization to aid analytics/routing
    tool_server = models.CharField(max_length=64, blank=True)
    tool_name = models.CharField(max_length=128, blank=True)
    server_config = models.ForeignKey(
        MCPServerConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enabled_tools",
    )

    enabled_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True, db_index=True)
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "tool_full_name"],
                name="unique_agent_tool_full_name",
            )
        ]
        indexes = [
            models.Index(fields=["agent", "last_used_at"], name="pa_en_tool_agent_lu_idx"),
            models.Index(fields=["tool_full_name"], name="pa_en_tool_name_idx"),
        ]
        ordering = ["-last_used_at", "-enabled_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"EnabledTool<{self.tool_full_name}> for {getattr(self.agent, 'name', 'agent')}"


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


class PersistentAgentWebhook(models.Model):
    """Outbound webhook endpoint configured for a persistent agent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="webhooks",
    )
    name = models.CharField(max_length=128)
    url = models.URLField(max_length=1024)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_response_status = models.IntegerField(null=True, blank=True)
    last_error_message = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "name"],
                name="uniq_agent_webhook_name",
            )
        ]
        indexes = [
            models.Index(fields=["agent", "created_at"], name="pa_webhook_agent_created_idx"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.name} → {self.url}"

    def clean(self):
        super().clean()
        if self.name:
            self.name = self.name.strip()
        if self.url:
            self.url = self.url.strip()

    def record_delivery(self, status_code: int | None, error_message: str | None = None) -> None:
        """Persist the latest delivery attempt metadata."""
        self.last_triggered_at = timezone.now()
        self.last_response_status = status_code
        self.last_error_message = (error_message or "")[:2000]
        self.save(
            update_fields=["last_triggered_at", "last_response_status", "last_error_message", "updated_at"],
        )


class PersistentAgentCommsEndpoint(models.Model):
    """Channel-agnostic communication endpoint (address/number/etc.)."""

    class EndpointManager(models.Manager):
        @staticmethod
        def _normalized(channel: str, address: str | None) -> str | None:
            return PersistentAgentCommsEndpoint.normalize_address(channel, address)

        def create(self, **kwargs):
            channel = kwargs.get("channel")
            if "address" in kwargs:
                kwargs["address"] = self._normalized(channel, kwargs["address"])
            return super().create(**kwargs)

        def get_or_create(self, defaults=None, **kwargs):
            channel = kwargs.get("channel")
            defaults = defaults.copy() if defaults else {}
            addr = None
            if "address__iexact" in kwargs:
                addr = kwargs.pop("address__iexact")
            elif "address" in kwargs:
                addr = kwargs.pop("address")
            if addr is not None:
                normalized = self._normalized(channel, addr)
                kwargs["address__iexact"] = normalized
                defaults.setdefault("address", normalized)
            if "address" in defaults:
                defaults["address"] = self._normalized(channel, defaults.get("address"))
            return super().get_or_create(defaults=defaults, **kwargs)

    objects = EndpointManager()

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
        constraints = [
            models.UniqueConstraint(
                Lower("address"),
                "channel",
                name="pa_endpoint_ci_channel_address",
            ),
        ]
        indexes = [
            models.Index(fields=["owner_agent", "channel"], name="pa_ep_agent_channel_idx"),
        ]
        ordering = ["channel", "address"]

    @staticmethod
    def normalize_address(channel: str, address: str | None) -> str | None:
        if address is None:
            return None
        normalized = address.strip()
        return normalized.lower()

    def save(self, *args, **kwargs):
        self.address = self.normalize_address(self.channel, self.address)
        super().save(*args, **kwargs)

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
    can_configure = models.BooleanField(
        default=False,
        help_text="Whether this contact can instruct the agent to update its charter or schedule"
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
        
        # Restrict organization-owned agents to email-only allowlists for now
        if self.channel == CommsChannel.SMS and self.agent.organization_id is not None:
            raise ValidationError({
                "channel": (
                    "Organization agents only support email addresses in allowlists. "
                    "Group SMS functionality is not yet available."
                )
            })

        # Enforce per-agent cap on *active* entries and pending invitations when activating entries
        enforce_cap = False
        if self.is_active:
            if self._state.adding:
                enforce_cap = True
            elif self.pk:
                previous_active = (
                    type(self)
                    .objects
                    .filter(pk=self.pk)
                    .values_list('is_active', flat=True)
                    .first()
                )
                enforce_cap = previous_active is False

        if enforce_cap:
            # Get the plan-based limit for this agent's owner
            from util.subscription_helper import get_user_max_contacts_per_agent
            cap = get_user_max_contacts_per_agent(
                self.agent.user,
                organization=self.agent.organization,
            )
            if cap <= 0:
                return
            
            try:
                counts = get_agent_contact_counts(self.agent)
                if counts is None:
                    return
                active_count = counts["active_total"]
                pending_count = counts["pending_total"]
                total_count = counts["total"]
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

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

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
    can_configure = models.BooleanField(
        default=False,
        help_text="Whether this contact can instruct the agent to update its charter or schedule"
    )

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
            cap = get_user_max_contacts_per_agent(
                self.agent.user,
                organization=self.agent.organization,
            )
            if cap <= 0:
                return
            
            try:
                counts = get_agent_contact_counts(self.agent)
                if counts is None:
                    return
                active_count = counts["active_total"]
                pending_count = counts["pending_total"]
                total_count = counts["total"]
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

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)
    
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
                "can_configure": self.can_configure,
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


def _generate_collaborator_invite_token() -> str:
    return secrets.token_urlsafe(32)


class AgentCollaborator(models.Model):
    """User collaborators who can chat with an agent and access shared files."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="collaborators",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_collaborations",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="agent_collaborators_invited",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["agent", "user"], name="uniq_agent_collaborator"),
        ]
        indexes = [
            models.Index(fields=["agent", "user"], name="collab_agent_user_idx"),
        ]
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        if self._state.adding:
            from util.subscription_helper import get_user_max_contacts_per_agent
            cap = get_user_max_contacts_per_agent(
                self.agent.user,
                organization=self.agent.organization,
            )
            if cap <= 0:
                return
            counts = get_agent_contact_counts(self.agent)
            if counts is None:
                return
            if counts["total"] >= cap:
                allow_pending_accept = False
                if counts["total"] == cap:
                    user_email = (getattr(self.user, "email", "") or "").strip()
                    if user_email:
                        allow_pending_accept = AgentCollaboratorInvite.objects.filter(
                            agent=self.agent,
                            email__iexact=user_email,
                            status=AgentCollaboratorInvite.InviteStatus.PENDING,
                            expires_at__gt=timezone.now(),
                        ).exists()
                if allow_pending_accept:
                    return
                pending_count = counts["pending_total"]
                raise ValidationError({
                    "agent": (
                        f"Cannot add more contacts. Maximum {cap} contacts "
                        f"allowed per agent for your plan (including {pending_count} pending invitations)."
                    )
                })

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"AgentCollaborator<{self.agent_id}:{self.user_id}>"


class AgentCollaboratorInvite(models.Model):
    """Invitation for a user to collaborate on an agent."""

    class InviteStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="collaborator_invites",
    )
    email = models.EmailField()
    token = models.CharField(max_length=64, unique=True, default=_generate_collaborator_invite_token)
    status = models.CharField(max_length=16, choices=InviteStatus.choices, default=InviteStatus.PENDING)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_collaborator_invites",
    )
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "email"],
                condition=models.Q(status__in=["pending", "accepted"]),
                name="uniq_active_collaborator_invite",
            ),
        ]
        indexes = [
            models.Index(fields=["token"], name="collab_invite_token_idx"),
            models.Index(fields=["agent", "status"], name="collab_invite_agent_status_idx"),
        ]
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        self.email = (self.email or "").strip().lower()
        if self._state.adding and self.status == self.InviteStatus.PENDING:
            from util.subscription_helper import get_user_max_contacts_per_agent
            cap = get_user_max_contacts_per_agent(
                self.agent.user,
                organization=self.agent.organization,
            )
            if cap <= 0:
                return
            counts = get_agent_contact_counts(self.agent)
            if counts is None:
                return
            if counts["total"] >= cap:
                pending_count = counts["pending_total"]
                raise ValidationError({
                    "agent": (
                        f"Cannot send more invitations. Maximum {cap} contacts "
                        f"allowed per agent for your plan (including {pending_count} pending invitations)."
                    )
                })

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    def is_expired(self):
        return timezone.now() > self.expires_at

    def can_be_accepted(self):
        return self.status == self.InviteStatus.PENDING and not self.is_expired()

    def accept(self, user):
        if not self.can_be_accepted():
            raise ValueError("This invitation cannot be accepted")

        collaborator, _ = AgentCollaborator.objects.get_or_create(
            agent=self.agent,
            user=user,
            defaults={"invited_by": self.invited_by},
        )

        self.status = self.InviteStatus.ACCEPTED
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "responded_at"])

        return collaborator

    def reject(self):
        if self.status != self.InviteStatus.PENDING:
            raise ValueError("This invitation has already been responded to")

        self.status = self.InviteStatus.REJECTED
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "responded_at"])

    def __str__(self):
        return f"CollaboratorInvite<{self.email}> for {self.agent_id} ({self.status})"


def get_agent_contact_counts(agent: PersistentAgent) -> dict[str, int] | None:
    try:
        now = timezone.now()
        allowlist_active = (
            CommsAllowlistEntry.objects
            .filter(agent=agent, is_active=True)
            .count()
        )
        allowlist_pending = (
            AgentAllowlistInvite.objects
            .filter(
                agent=agent,
                status=AgentAllowlistInvite.InviteStatus.PENDING,
                expires_at__gt=now,
            )
            .count()
        )
        collaborators_active = AgentCollaborator.objects.filter(agent=agent).count()
        collaborators_pending = (
            AgentCollaboratorInvite.objects
            .filter(
                agent=agent,
                status=AgentCollaboratorInvite.InviteStatus.PENDING,
                expires_at__gt=now,
            )
            .count()
        )
        active_total = allowlist_active + collaborators_active
        pending_total = allowlist_pending + collaborators_pending
        return {
            "allowlist_active": allowlist_active,
            "allowlist_pending": allowlist_pending,
            "collaborators_active": collaborators_active,
            "collaborators_pending": collaborators_pending,
            "active_total": active_total,
            "pending_total": pending_total,
            "total": active_total + pending_total,
        }
    except Exception:
        logger.error(
            "Error checking contact counts for agent %s",
            getattr(agent, "id", None),
            exc_info=True,
        )
        return None


def _generate_transfer_token() -> str:
    return secrets.token_urlsafe(32)


class AgentTransferInvite(models.Model):
    """Invitation representing a pending agent ownership transfer."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        PersistentAgent,
        on_delete=models.CASCADE,
        related_name="transfer_invites",
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transfer_invites_sent",
    )
    to_email = models.EmailField()
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfer_invites_received",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    token = models.CharField(max_length=64, unique=True, default=_generate_transfer_token)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["agent", "status"], name="ati_agent_status_idx"),
            models.Index(fields=["to_email", "status"], name="ati_email_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["agent"],
                condition=models.Q(status="pending"),
                name="uniq_pending_agent_transfer_invite",
            ),
        ]

    def clean(self):
        super().clean()
        self.to_email = (self.to_email or "").strip().lower()

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"TransferInvite<{self.agent_id}->{self.to_email} ({self.status})>"


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
    request_configure = models.BooleanField(
        default=False,
        help_text="Whether to grant this contact authority to update agent charter/schedule"
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
                allow_outbound=self.request_outbound,
                can_configure=self.request_configure,
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
            can_configure=self.request_configure,
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
        OAUTH2 = "oauth2", "OAuth 2.0"

    class ImapSecurity(models.TextChoices):
        SSL = "ssl", "SSL"
        STARTTLS = "starttls", "STARTTLS"
        NONE = "none", "None"

    class ImapAuthMode(models.TextChoices):
        NONE = "none", "None"
        LOGIN = "login", "LOGIN"
        OAUTH2 = "oauth2", "OAuth 2.0"

    class ConnectionMode(models.TextChoices):
        CUSTOM = "custom", "Custom SMTP/IMAP"
        OAUTH2 = "oauth2", "OAuth 2.0"

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
    imap_auth = models.CharField(
        max_length=16, choices=ImapAuthMode.choices, default=ImapAuthMode.LOGIN
    )
    imap_folder = models.CharField(max_length=128, default="INBOX")
    is_inbound_enabled = models.BooleanField(default=False)
    # Optional per-account toggle to enable IDLE watchers for lower latency (keeps polling as source of truth)
    imap_idle_enabled = models.BooleanField(default=False)

    poll_interval_sec = models.PositiveIntegerField(default=120)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_seen_uid = models.CharField(max_length=64, blank=True)
    backoff_until = models.DateTimeField(null=True, blank=True)
    connection_mode = models.CharField(
        max_length=16, choices=ConnectionMode.choices, default=ConnectionMode.OAUTH2
    )

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
                if self.smtp_auth == self.AuthMode.OAUTH2:
                    if not getattr(self, "oauth_credential", None):
                        raise ValidationError({"smtp_auth": "Connect OAuth before enabling outbound SMTP"})
                elif not self.smtp_password_encrypted:
                    raise ValidationError({"smtp_password_encrypted": "Password required for authenticated SMTP"})

            # Gate: require a successful connection test before enabling
            if not self.connection_last_ok_at:
                raise ValidationError({
                    "is_outbound_enabled": "Run Test SMTP and ensure success before enabling outbound."
                })

        if self.is_inbound_enabled and self.imap_auth == self.ImapAuthMode.OAUTH2:
            if not self.imap_username:
                raise ValidationError({"imap_username": "Username required for authenticated IMAP"})
            if not getattr(self, "oauth_credential", None):
                raise ValidationError({"imap_auth": "Connect OAuth before enabling inbound IMAP"})

        if self.connection_mode == self.ConnectionMode.OAUTH2:
            if self.is_outbound_enabled and self.smtp_auth != self.AuthMode.OAUTH2:
                raise ValidationError({"smtp_auth": "OAuth mode requires OAuth 2.0 for SMTP"})
            if self.is_inbound_enabled and self.imap_auth != self.ImapAuthMode.OAUTH2:
                raise ValidationError({"imap_auth": "OAuth mode requires OAuth 2.0 for IMAP"})
        elif self.connection_mode == self.ConnectionMode.CUSTOM:
            if self.smtp_auth == self.AuthMode.OAUTH2:
                raise ValidationError({"smtp_auth": "Custom mode does not support OAuth 2.0"})
            if self.imap_auth == self.ImapAuthMode.OAUTH2:
                raise ValidationError({"imap_auth": "Custom mode does not support OAuth 2.0"})


class AgentEmailOAuthCredential(models.Model):
    """Encrypted OAuth credential store for agent email accounts."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.OneToOneField(
        AgentEmailAccount,
        on_delete=models.CASCADE,
        related_name="oauth_credential",
    )
    organization = models.ForeignKey(
        "Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="agent_email_oauth_credentials",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_email_oauth_credentials",
    )
    provider = models.CharField(max_length=64, blank=True)
    client_id = models.CharField(max_length=256, blank=True)
    client_secret_encrypted = models.BinaryField(null=True, blank=True)
    access_token_encrypted = models.BinaryField(null=True, blank=True)
    refresh_token_encrypted = models.BinaryField(null=True, blank=True)
    id_token_encrypted = models.BinaryField(null=True, blank=True)
    token_type = models.CharField(max_length=32, blank=True)
    scope = models.CharField(max_length=512, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization"], name="email_oauth_cred_org_idx"),
            models.Index(fields=["user"], name="email_oauth_cred_user_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"AgentEmailOAuthCredential<{self.account_id}>"

    @staticmethod
    def _encrypt_text(value: Optional[str]) -> Optional[bytes]:
        if not value:
            return None
        from .encryption import SecretsEncryption

        return SecretsEncryption.encrypt_value(value)

    @staticmethod
    def _decrypt_text(payload: Optional[bytes]) -> str:
        if not payload:
            return ""
        try:
            from .encryption import SecretsEncryption

            return SecretsEncryption.decrypt_value(payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to decrypt email OAuth credential payload")
            raise

    @property
    def client_secret(self) -> str:
        return self._decrypt_text(self.client_secret_encrypted)

    @client_secret.setter
    def client_secret(self, value: Optional[str]) -> None:
        self.client_secret_encrypted = self._encrypt_text(value)

    @property
    def access_token(self) -> str:
        return self._decrypt_text(self.access_token_encrypted)

    @access_token.setter
    def access_token(self, value: Optional[str]) -> None:
        self.access_token_encrypted = self._encrypt_text(value)

    @property
    def refresh_token(self) -> str:
        return self._decrypt_text(self.refresh_token_encrypted)

    @refresh_token.setter
    def refresh_token(self, value: Optional[str]) -> None:
        self.refresh_token_encrypted = self._encrypt_text(value)

    @property
    def id_token(self) -> str:
        return self._decrypt_text(self.id_token_encrypted)

    @id_token.setter
    def id_token(self, value: Optional[str]) -> None:
        self.id_token_encrypted = self._encrypt_text(value)


class AgentEmailOAuthSession(models.Model):
    """Ephemeral OAuth session state for agent email authentication flows."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        AgentEmailAccount,
        on_delete=models.CASCADE,
        related_name="oauth_sessions",
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_email_oauth_sessions",
    )
    organization = models.ForeignKey(
        "Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="agent_email_oauth_sessions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_email_oauth_user_sessions",
    )
    state = models.CharField(max_length=256, unique=True)
    redirect_uri = models.CharField(max_length=512, blank=True)
    scope = models.CharField(max_length=512, blank=True)
    code_challenge = models.CharField(max_length=256, blank=True)
    code_challenge_method = models.CharField(max_length=16, blank=True)
    token_endpoint = models.CharField(max_length=512, blank=True)
    client_id = models.CharField(max_length=256, blank=True)
    client_secret_encrypted = models.BinaryField(null=True, blank=True)
    code_verifier_encrypted = models.BinaryField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"], name="email_oauth_session_exp_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"AgentEmailOAuthSession<{self.account_id} state={self.state}>"

    @staticmethod
    def _encrypt_text(value: Optional[str]) -> Optional[bytes]:
        if not value:
            return None
        from .encryption import SecretsEncryption

        return SecretsEncryption.encrypt_value(value)

    @staticmethod
    def _decrypt_text(payload: Optional[bytes]) -> str:
        if not payload:
            return ""
        try:
            from .encryption import SecretsEncryption

            return SecretsEncryption.decrypt_value(payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to decrypt email OAuth session payload")
            raise

    @property
    def client_secret(self) -> str:
        return self._decrypt_text(self.client_secret_encrypted)

    @client_secret.setter
    def client_secret(self, value: Optional[str]) -> None:
        self.client_secret_encrypted = self._encrypt_text(value)

    @property
    def code_verifier(self) -> str:
        return self._decrypt_text(self.code_verifier_encrypted)

    @code_verifier.setter
    def code_verifier(self, value: Optional[str]) -> None:
        self.code_verifier_encrypted = self._encrypt_text(value)


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
    is_peer_dm = models.BooleanField(
        default=False,
        help_text="Whether this conversation stores direct messages between agents.",
    )
    peer_link = models.OneToOneField(
        "AgentPeerLink",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversation",
        help_text="Peer link associated with this direct message thread, when applicable.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["channel", "address"], name="pa_conv_channel_addr_idx"),
            models.Index(fields=["is_peer_dm"], name="pa_conv_peer_dm_idx"),
        ]
        ordering = ["-id"]

    def __str__(self):
        return f"Conversation<{self.channel}:{self.address}>"

    def clean(self):
        super().clean()
        if self.is_peer_dm and not self.peer_link_id:
            raise ValidationError({
                "peer_link": "Peer DM conversations must reference a peer link."
            })
        if self.peer_link_id and not self.is_peer_dm:
            # Automatically mark DM conversations when linked.
            self.is_peer_dm = True

    def save(self, *args, **kwargs):
        if not kwargs.get("raw"):
            self.full_clean()
        return super().save(*args, **kwargs)


class AgentPeerLink(models.Model):
    """Symmetric link allowing direct messaging between two agents."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_a = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="peer_links_initiated",
    )
    agent_b = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="peer_links_received",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_agent_peer_links",
    )
    messages_per_window = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(500)],
        help_text="Number of peer messages allowed per rolling window.",
    )
    window_hours = models.PositiveIntegerField(
        default=6,
        validators=[MinValueValidator(1), MaxValueValidator(168)],
        help_text="Length of the quota window in hours.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Feature-flag style toggle to enable peer messaging for this link.",
    )
    feature_flag = models.CharField(
        max_length=64,
        blank=True,
        help_text="Optional rollout flag label controlling this peer link.",
    )
    agent_a_endpoint = models.ForeignKey(
        "PersistentAgentCommsEndpoint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="peer_link_agent_a_endpoints",
        help_text="Preferred endpoint for agent A when initiating peer DMs.",
    )
    agent_b_endpoint = models.ForeignKey(
        "PersistentAgentCommsEndpoint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="peer_link_agent_b_endpoints",
        help_text="Preferred endpoint for agent B when initiating peer DMs.",
    )
    pair_key = models.CharField(
        max_length=96,
        unique=True,
        editable=False,
        help_text="Deterministic key built from the sorted agent IDs for uniqueness.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["pair_key"], name="agent_peer_pair_key"),
            models.Index(fields=["is_enabled"], name="agent_peer_enabled_idx"),
        ]

    def __str__(self) -> str:
        return f"PeerLink<{self.agent_a_id}->{self.agent_b_id}>"

    @staticmethod
    def build_pair_key(agent_a_id: uuid.UUID | str, agent_b_id: uuid.UUID | str) -> str:
        """Return stable pair key for two agent IDs."""
        return "::".join(sorted([str(agent_a_id), str(agent_b_id)]))

    def get_other_agent(self, agent: "PersistentAgent") -> PersistentAgent | None:
        """Return the counterpart agent for the provided agent instance."""
        if not agent:
            return None
        if agent.id == self.agent_a_id:
            return self.agent_b
        if agent.id == self.agent_b_id:
            return self.agent_a
        return None

    def clean(self):
        super().clean()

        if not self.agent_a_id or not self.agent_b_id:
            raise ValidationError("Both agents are required for a peer link.")
        if self.agent_a_id == self.agent_b_id:
            raise ValidationError("Cannot create a peer link between the same agent.")

        agent_a = self.agent_a
        agent_b = self.agent_b

        if not agent_a or not agent_b:
            raise ValidationError("Agents must exist to create a peer link.")

        same_owner = agent_a.user_id and agent_a.user_id == agent_b.user_id
        same_org = (
            agent_a.organization_id
            and agent_b.organization_id
            and agent_a.organization_id == agent_b.organization_id
        )
        if not same_owner and not same_org:
            raise ValidationError(
                "Agents must share the same owner or organization to link."
            )

        if self.agent_a_endpoint and self.agent_a_endpoint.owner_agent_id != agent_a.id:
            raise ValidationError(
                {"agent_a_endpoint": "Preferred endpoint must belong to agent A."}
            )
        if self.agent_b_endpoint and self.agent_b_endpoint.owner_agent_id != agent_b.id:
            raise ValidationError(
                {"agent_b_endpoint": "Preferred endpoint must belong to agent B."}
            )

    def save(self, *args, **kwargs):
        if kwargs.get("raw"):
            return super().save(*args, **kwargs)

        if not self.agent_a_id or not self.agent_b_id:
            raise ValidationError("Both agents are required for a peer link.")

        self.pair_key = self.build_pair_key(self.agent_a_id, self.agent_b_id)

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            update_fields.add("pair_key")
            kwargs["update_fields"] = list(update_fields)

        self.full_clean()
        return super().save(*args, **kwargs)


class AgentCommPeerState(models.Model):
    """Rolling credit bucket tracking peer DM quotas per channel."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    link = models.ForeignKey(
        AgentPeerLink,
        on_delete=models.CASCADE,
        related_name="communication_states",
    )
    channel = models.CharField(max_length=32, choices=CommsChannel.choices)
    messages_per_window = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(500)],
    )
    window_hours = models.PositiveIntegerField(
        default=6,
        validators=[MinValueValidator(1), MaxValueValidator(168)],
    )
    credits_remaining = models.PositiveIntegerField(default=0)
    window_reset_at = models.DateTimeField()
    last_message_at = models.DateTimeField(null=True, blank=True)
    debounce_seconds = models.PositiveIntegerField(default=5)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("link", "channel")
        indexes = [
            models.Index(fields=["link", "channel"], name="agent_peer_state_idx"),
            models.Index(fields=["window_reset_at"], name="agent_peer_reset_idx"),
        ]

    def __str__(self) -> str:
        return f"PeerState<link={self.link_id}, channel={self.channel}>"

    def clean(self):
        super().clean()
        if self.messages_per_window < 1:
            raise ValidationError("messages_per_window must be positive.")
        if self.window_hours < 1:
            raise ValidationError("window_hours must be positive.")
        if self.debounce_seconds < 0:
            raise ValidationError("debounce_seconds cannot be negative.")

    def reset_window(self) -> None:
        """Reset the rolling quota window."""
        now = timezone.now()
        self.window_reset_at = now + timedelta(hours=self.window_hours)
        self.credits_remaining = self.messages_per_window
        self.save(update_fields=["window_reset_at", "credits_remaining", "updated_at"])

    def save(self, *args, **kwargs):
        if kwargs.get("raw"):
            return super().save(*args, **kwargs)

        now = timezone.now()
        if not self.window_reset_at:
            self.window_reset_at = now + timedelta(hours=self.window_hours)
        if self._state.adding and not self.credits_remaining:
            self.credits_remaining = self.messages_per_window

        self.full_clean()
        return super().save(*args, **kwargs)


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
    peer_agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="peer_agent_messages",
        help_text="The other agent participating in a peer DM, when applicable.",
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
    latest_error_message = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["conversation", "-seq"], name="pa_msg_conv_seq_idx"),
            models.Index(fields=["conversation", "-timestamp"], name="pa_msg_conv_ts_idx"),
            models.Index(fields=["from_endpoint", "to_endpoint", "-seq"], name="pa_msg_endpoints_seq_idx"),
            models.Index(fields=["from_endpoint", "-timestamp"], name="pa_msg_from_ts_idx"),
            models.Index(fields=["owner_agent", "-timestamp"], name="pa_msg_agent_ts_idx"),
            models.Index(fields=["latest_status"], name="pa_msg_latest_status_idx"),
            models.Index(fields=["peer_agent", "-timestamp"], name="pa_msg_peer_agent_idx"),
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


class PersistentAgentEmailFooter(models.Model):
    """Reusable snippets appended to outbound emails for eligible agents."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, help_text="Label to identify this footer in admin.")
    html_content = models.TextField(help_text="HTML snippet appended to the email template.")
    text_content = models.TextField(help_text="Plaintext snippet appended to the email body.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Persistent Agent Email Footer"
        verbose_name_plural = "Persistent Agent Email Footers"

    def __str__(self) -> str:
        return self.name


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


class PersistentAgentWebSession(models.Model):
    """Represents an interactive web chat session between a user and an agent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="web_sessions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_web_sessions",
    )
    session_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    started_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_source = models.CharField(max_length=32, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("agent", "user")
        indexes = [
            models.Index(fields=["agent", "last_seen_at"], name="pa_web_session_agent_idx"),
            models.Index(fields=["session_key"], name="pa_web_session_key_idx"),
            models.Index(fields=["ended_at", "last_seen_at"], name="pa_web_session_end_idx"),
        ]

    def __str__(self) -> str:
        return f"WebSession<{self.agent_id}:{self.user_id}:{self.session_key}>"

class PersistentAgentCompletion(models.Model):
    """Represents a single LLM completion within a persistent agent run."""

    class CompletionType(models.TextChoices):
        ORCHESTRATOR = ("orchestrator", "Orchestrator")
        COMPACTION = ("compaction", "Comms Compaction")
        STEP_COMPACTION = ("step_compaction", "Step Compaction")
        PROMPT_SUMMARIZATION = ("prompt_summarization", "Prompt Summarization")
        TAG = ("tag", "Tag Generation")
        SHORT_DESCRIPTION = ("short_description", "Short Description")
        MINI_DESCRIPTION = ("mini_description", "Mini Description")
        TOOL_SEARCH = ("tool_search", "Tool Search")
        TEMPLATE_CLONE = ("template_clone", "Template Clone")
        OTHER = ("other", "Other")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="completions",
        help_text="Agent that triggered this LLM completion.",
    )
    eval_run = models.ForeignKey(
        "EvalRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_completions",
        help_text="Eval run context for this completion, when applicable.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completion_type = models.CharField(
        max_length=64,
        choices=CompletionType.choices,
        default=CompletionType.ORCHESTRATOR,
        help_text="Origin of the completion (orchestrator loop, compaction, tag generation, etc.).",
    )
    response_id = models.CharField(
        max_length=256,
        null=True,
        blank=True,
        help_text="Provider response identifier when available.",
    )
    request_duration_ms = models.IntegerField(
        null=True,
        blank=True,
        help_text="Time in milliseconds spent waiting for the completion response.",
    )

    prompt_tokens = models.IntegerField(null=True, blank=True)
    completion_tokens = models.IntegerField(null=True, blank=True)
    total_tokens = models.IntegerField(null=True, blank=True)
    cached_tokens = models.IntegerField(null=True, blank=True)
    llm_model = models.CharField(max_length=256, null=True, blank=True)
    llm_provider = models.CharField(max_length=128, null=True, blank=True)
    thinking_content = models.TextField(
        null=True,
        blank=True,
        help_text="Reasoning/thinking content returned by the LLM when available.",
    )
    input_cost_total = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Total USD cost for prompt tokens (cached + uncached).",
    )
    input_cost_uncached = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="USD cost for uncached prompt tokens.",
    )
    input_cost_cached = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="USD cost for cached prompt tokens.",
    )
    output_cost = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="USD cost for completion tokens.",
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Total USD cost (input + output).",
    )

    credits_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Credits consumed for this completion (if charged).",
    )
    billed = models.BooleanField(default=False, help_text="True once credits were consumed for this completion.")
    billed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["agent", "-created_at"], name="pa_completion_recent_idx"),
        ]

    def __str__(self):
        return f"Completion[{self.completion_type}] {self.llm_model or 'unknown'} @ {self.created_at}"


class ContentSummaryCache(models.Model):
    """Cached LLM summaries keyed by content hash and summary type."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content_hash = models.CharField(max_length=64, db_index=True)
    summary_type = models.CharField(max_length=64, db_index=True)
    summary = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["content_hash", "summary_type"],
                name="content_summary_cache_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["summary_type", "content_hash"],
                name="content_summary_type_hash_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"ContentSummaryCache<{self.summary_type}>"


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
    completion = models.ForeignKey(
        "PersistentAgentCompletion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="steps",
        help_text="LLM completion that produced this step (if applicable).",
    )

    eval_run = models.ForeignKey(
        "EvalRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_steps",
        help_text="Eval run context for this step, when applicable.",
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

    # Credits charged for this step (for audit). If not provided, defaults to configured per‑task cost.
    credits_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Credits charged for this step; defaults to configured per‑task cost.",
    )
    # Billing rollup flag: has this step been included in a Stripe meter rollup?
    metered = models.BooleanField(default=False, db_index=True, help_text="Marked true once included in Stripe metering rollup.")
    # Temporary batch key used to reserve rows for an idempotent metering batch
    meter_batch_key = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Fast lookup of recent steps for an agent
            models.Index(fields=["agent", "-created_at"], name="pa_step_recent_idx"),
            # Ascending order index to support compaction filter/order queries
            models.Index(fields=["agent", "created_at", "id"], name="pa_step_agent_ts_idx"),
        ]

    def __str__(self):
        preview = (self.description or "").replace("\n", " ")[:60]
        return f"Step {preview}..."

    def save(self, *args, **kwargs):
        completion_to_mark = None
        completion_mark_amount = None

        if self.eval_run_id is None:
            completion_obj = getattr(self, "completion", None) if self.completion_id else None
            if completion_obj and completion_obj.eval_run_id:
                self.eval_run_id = completion_obj.eval_run_id

        # On creation, optionally consume credits for chargeable steps only.
        if self._state.adding:
            from django.core.exceptions import ValidationError

            owner = None
            if self.agent and getattr(self.agent, 'organization', None):
                owner = self.agent.organization
            elif self.agent:
                owner = self.agent.user

            completion_obj = getattr(self, "completion", None) if self.completion_id else None
            completion_requires_billing = bool(completion_obj and not completion_obj.billed)
            completion_to_mark = completion_obj if completion_requires_billing else None
            completion_mark_amount = None

            should_charge = self.credits_cost is not None or completion_requires_billing

            if owner is not None and should_charge:
                if self.task_credit_id is not None:
                    # Credits were already consumed upstream (e.g., just-in-time tool gating).
                    # Do NOT consume again; keep the existing linkage for audit.
                    if completion_to_mark is not None and self.credits_cost is not None:
                        completion_mark_amount = self.credits_cost
                else:
                    default_cost = get_default_task_credit_cost()
                    if self.credits_cost is not None:
                        amount = self.credits_cost
                    else:
                        amount = _apply_tier_multiplier(self.agent, default_cost)
                        self.credits_cost = amount
                    result = TaskCreditService.check_and_consume_credit_for_owner(owner, amount=amount)

                    if not result.get('success'):
                        raise ValidationError({"quota": result.get('error_message')})

                    self.task_credit = result.get('credit')
                    if completion_to_mark is not None:
                        completion_mark_amount = amount
            elif completion_to_mark is not None and self.credits_cost is not None:
                # Owner-less steps (system agents) may still want the completion marked with explicit cost.
                completion_mark_amount = self.credits_cost

        result = super().save(*args, **kwargs)
        if completion_to_mark is not None and not completion_to_mark.billed:
            completion_to_mark.billed = True
            completion_to_mark.billed_at = timezone.now()
            if completion_mark_amount is not None:
                completion_to_mark.credits_cost = completion_mark_amount
                update_fields = ["billed", "billed_at", "credits_cost"]
            else:
                update_fields = ["billed", "billed_at"]
            completion_to_mark.save(update_fields=update_fields)
        return result


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
    execution_duration_ms = models.IntegerField(
        null=True,
        blank=True,
        help_text="Elapsed time in milliseconds for executing the tool call.",
    )
    status = models.CharField(
        max_length=32,
        default="complete",
        blank=True,
        help_text="Execution status for the tool call (pending, complete, error).",
    )

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
        PEER_LINK_CREATED = "PEER_LINK_CREATED", "Peer Link Created"
        SNAPSHOT = "SNAPSHOT", "Snapshot"
        CREDENTIALS_PROVIDED = "CREDENTIALS_PROVIDED", "Credentials Provided"
        CONTACTS_APPROVED = "CONTACTS_APPROVED", "Contacts Approved"
        COLLABORATOR_ADDED = "COLLABORATOR_ADDED", "Collaborator Added"
        LLM_CONFIGURATION_REQUIRED = "LLM_CONFIGURATION_REQUIRED", "LLM Configuration Required"
        PROACTIVE_TRIGGER = "PROACTIVE_TRIGGER", "Proactive Trigger"
        SYSTEM_DIRECTIVE = "SYSTEM_DIRECTIVE", "System Directive"
        BURN_RATE_COOLDOWN = "BURN_RATE_COOLDOWN", "Burn Rate Cooldown"
        RATE_LIMIT = "RATE_LIMIT", "Rate Limit"
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


class PersistentAgentPromptArchive(models.Model):
    """Metadata for archived rendered prompts stored outside the primary DB."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="prompt_archives",
    )
    rendered_at = models.DateTimeField(help_text="Timestamp when the prompt was rendered.")
    storage_key = models.CharField(max_length=512, help_text="Object storage key for the compressed prompt payload.")
    raw_bytes = models.IntegerField(help_text="Uncompressed payload size in bytes.")
    compressed_bytes = models.IntegerField(help_text="Compressed payload size in bytes.")
    tokens_before = models.IntegerField(help_text="Token count before prompt fitting.")
    tokens_after = models.IntegerField(help_text="Token count after prompt fitting.")
    tokens_saved = models.IntegerField(help_text="Tokens removed during fitting.")
    step = models.OneToOneField(
        "PersistentAgentStep",
        on_delete=models.CASCADE,
        related_name="llm_prompt_archive",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-rendered_at"]
        indexes = [
            models.Index(fields=["agent", "-rendered_at"], name="pa_prompt_archive_recent_idx"),
            models.Index(fields=["rendered_at"], name="pa_prompt_archive_rendered_idx"),
        ]

    def delete(self, using=None, keep_parents=False):
        """Remove the archived payload from storage before deleting the row."""
        storage_key = self.storage_key
        if storage_key:
            try:
                if default_storage.exists(storage_key):
                    default_storage.delete(storage_key)
            except Exception:
                logger.exception("Failed to delete prompt archive payload at %s", storage_key)
        return super().delete(using=using, keep_parents=keep_parents)

    def __str__(self):
        return f"PromptArchive<{self.agent_id}> {self.rendered_at.isoformat()} key={self.storage_key}"


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


class PipedreamConnectSession(models.Model):
    """Tracks a Pipedream Connect token lifecycle for an agent."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="pipedream_connect_sessions",
    )

    # Identity scoping used when creating the token
    external_user_id = models.CharField(max_length=64)
    conversation_id = models.CharField(max_length=64)

    # App this session is intended to connect (e.g., google_sheets)
    app_slug = models.CharField(max_length=64)

    # Short‑lived token and link returned by Connect API
    # null=True so multiple pending sessions can exist (NULLs don't violate unique constraint)
    connect_token = models.CharField(max_length=128, unique=True, null=True, blank=True, default=None)
    connect_link_url = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Webhook correlation and security
    webhook_secret = models.CharField(max_length=64)

    # Outcome
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    account_id = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["agent", "status", "-created_at"], name="pd_connect_agent_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"PipedreamConnectSession<{self.app_slug}|{self.status}>"


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
        max_length=512,
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


class ComputeSnapshot(models.Model):
    """Disk-only snapshot metadata for sandbox compute sessions."""

    class Status(models.TextChoices):
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.CASCADE,
        related_name="compute_snapshots",
    )
    k8s_snapshot_name = models.CharField(max_length=255)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.READY,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["agent", "created_at"], name="compute_snapshot_agent_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"ComputeSnapshot<{self.id} agent={self.agent_id} status={self.status}>"


class AgentComputeSession(models.Model):
    """Control-plane metadata for a per-agent sandbox session."""

    class State(models.TextChoices):
        RUNNING = "running", "Running"
        IDLE_STOPPING = "idle_stopping", "Idle Stopping"
        STOPPED = "stopped", "Stopped"
        ERROR = "error", "Error"

    agent = models.OneToOneField(
        "PersistentAgent",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="compute_session",
    )
    pod_name = models.CharField(max_length=128, blank=True)
    namespace = models.CharField(max_length=128, blank=True)
    state = models.CharField(
        max_length=32,
        choices=State.choices,
        default=State.STOPPED,
    )
    last_activity_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_filespace_sync_at = models.DateTimeField(null=True, blank=True)
    proxy_server = models.ForeignKey(
        "ProxyServer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compute_sessions",
    )
    workspace_snapshot = models.ForeignKey(
        "ComputeSnapshot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state"], name="compute_session_state_idx"),
            models.Index(fields=["lease_expires_at"], name="compute_session_lease_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"AgentComputeSession<{self.agent_id} state={self.state}>"


# Auto-provision a default filespace for new PersistentAgents
@receiver(pre_save, sender=PersistentAgent)
def enforce_org_seats_before_save(sender, instance: PersistentAgent, **kwargs):
    """Prevent creating or reassigning org-owned agents without purchased seats."""
    if not instance.organization_id:
        return

    original_org_id = None
    if instance.pk:
        original_org_id = sender.objects.filter(pk=instance.pk).values_list('organization_id', flat=True).first()

    if instance.pk and original_org_id == instance.organization_id:
        return

    instance._validate_org_seats()


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


@receiver(post_save, sender=Organization)
def initialize_organization_billing(sender, instance, created, **kwargs):
    if created:
        # Ensure every organization starts with a billing record so downstream code can rely on it.
        OrganizationBilling.objects.get_or_create(
            organization=instance,
            defaults={'billing_cycle_anchor': timezone.now().day},
        )

class OrganizationMembership(models.Model):
    class OrgRole(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        SOLUTIONS_PARTNER = "solutions_partner", "Solutions Partner"
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

    def clean(self):
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        super().clean()

        if self.org_id is None:
            return

        billing = getattr(self.org, "billing", None)
        if billing is None:
            raise ValidationError({"org": "Organization is missing billing configuration."})

        now = timezone.now()

        founder_allowance = 1

        active_members = OrganizationMembership.objects.filter(
            org_id=self.org_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).exclude(role=OrganizationMembership.OrgRole.SOLUTIONS_PARTNER).count()

        pending_invites_qs = OrganizationInvite.objects.filter(
            org_id=self.org_id,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gte=now,
        ).exclude(role=OrganizationMembership.OrgRole.SOLUTIONS_PARTNER)

        if self.pk:
            pending_invites_qs = pending_invites_qs.exclude(pk=self.pk)

        pending_invites = pending_invites_qs.count()

        will_reserve_seat = (
            self.accepted_at is None
            and self.revoked_at is None
            and (self.expires_at or now) >= now
            and self.role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
        )

        seats_required = max(active_members - founder_allowance, 0) + pending_invites + (1 if will_reserve_seat else 0)

        if seats_required > billing.purchased_seats:
            raise ValidationError({
                "org": "No seats available for this invitation. Increase seat count or revoke existing invites.",
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class EvalSuiteRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        ERRORED = "errored", "Errored"

    class AgentStrategy(models.TextChoices):
        EPHEMERAL_PER_SCENARIO = "ephemeral_per_scenario", "Ephemeral per scenario"
        REUSE_AGENT = "reuse_agent", "Reuse provided agent"

    class RunType(models.TextChoices):
        ONE_OFF = "one_off", "One-off"
        OFFICIAL = "official", "Official"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    suite_slug = models.CharField(max_length=200)
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    run_type = models.CharField(
        max_length=20,
        choices=RunType.choices,
        default=RunType.ONE_OFF,
        help_text="One-off runs are ad-hoc; official runs are tracked over time.",
    )
    requested_runs = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text="How many times to repeat each scenario for this suite run.",
    )
    agent_strategy = models.CharField(
        max_length=40,
        choices=AgentStrategy.choices,
        default=AgentStrategy.EPHEMERAL_PER_SCENARIO,
    )
    shared_agent = models.ForeignKey(
        "PersistentAgent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Agent reused across all scenarios if agent_strategy is reuse_agent.",
    )
    llm_routing_profile = models.ForeignKey(
        "LLMRoutingProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_suite_runs",
        help_text="LLM routing profile to use for this suite. If null, uses active profile.",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.suite_slug} ({self.id})"


class EvalRun(models.Model):
    RunType = EvalSuiteRun.RunType

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        ERRORED = "errored", "Errored"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    suite_run = models.ForeignKey(
        EvalSuiteRun,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="runs",
    )
    scenario_slug = models.CharField(max_length=200)
    scenario_version = models.CharField(max_length=50, blank=True)
    scenario_fingerprint = models.CharField(
        max_length=16,
        blank=True,
        db_index=True,
        help_text="AST hash of scenario code for comparability tracking.",
    )
    agent = models.ForeignKey(PersistentAgent, on_delete=models.CASCADE)
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    run_type = models.CharField(
        max_length=20,
        choices=RunType.choices,
        default=RunType.ONE_OFF,
        help_text="One-off runs are ad-hoc; official runs are tracked over time.",
    )
    
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    
    # Execution context
    budget_id = models.CharField(max_length=100, blank=True)
    branch_id = models.CharField(max_length=100, blank=True)
    code_version = models.CharField(
        max_length=12,
        blank=True,
        help_text="Git commit hash at run time.",
    )
    code_branch = models.CharField(
        max_length=128,
        blank=True,
        help_text="Git branch name at run time.",
    )
    llm_routing_profile = models.ForeignKey(
        "LLMRoutingProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_runs",
        help_text="LLM routing profile used for this run.",
    )
    llm_routing_profile_name = models.CharField(
        max_length=64,
        blank=True,
        help_text="Snapshot of profile name at run time (preserved if profile deleted).",
    )
    primary_model = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        help_text="Primary LLM model used (e.g., 'claude-sonnet-4'). Denormalized for comparison queries.",
    )

    # Metrics snapshots (aggregated after run)
    tokens_used = models.IntegerField(default=0)
    credits_cost = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0"))
    completion_count = models.IntegerField(default=0)
    step_count = models.IntegerField(default=0)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    cached_tokens = models.IntegerField(default=0)
    input_cost_total = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    input_cost_uncached = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    input_cost_cached = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    output_cost = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    total_cost = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.scenario_slug} ({self.id})"


class EvalRunTask(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        ERRORED = "errored", "Errored"
        SKIPPED = "skipped", "Skipped"

    run = models.ForeignKey(EvalRun, on_delete=models.CASCADE, related_name='tasks')
    sequence = models.IntegerField()
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    assertion_type = models.CharField(max_length=50)
    
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    
    # Summaries
    expected_summary = models.TextField(blank=True)
    observed_summary = models.TextField(blank=True)

    # Artifact links
    first_step = models.ForeignKey(PersistentAgentStep, on_delete=models.SET_NULL, null=True, blank=True)
    first_message = models.ForeignKey(PersistentAgentMessage, on_delete=models.SET_NULL, null=True, blank=True)
    first_browser_task = models.ForeignKey(BrowserUseAgentTask, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Specific assertion data
    tool_called = models.CharField(max_length=200, blank=True)
    charter_before = models.TextField(blank=True)
    charter_after = models.TextField(blank=True)
    schedule_before = models.TextField(blank=True)
    schedule_after = models.TextField(blank=True)
    llm_question = models.TextField(blank=True)
    llm_answer = models.TextField(blank=True)
    llm_model = models.CharField(max_length=100, blank=True)

    # Aggregated usage/cost for this task window
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    cached_tokens = models.IntegerField(default=0)
    input_cost_total = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    input_cost_uncached = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    input_cost_cached = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    output_cost = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    total_cost = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    credits_cost = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sequence']

    def __str__(self):
        return f"{self.run.scenario_slug} - {self.name} ({self.status})"


# =============================================================================
# Signals to touch LLMRoutingProfile when child models change
# This invalidates the "preferred provider" cache for agents
# =============================================================================

def _touch_routing_profile(profile):
    """Touch the profile's updated_at to invalidate preferred provider cache."""
    if profile:
        LLMRoutingProfile.objects.filter(pk=profile.pk).update(updated_at=timezone.now())


@receiver(post_save, sender=ProfileTokenRange)
@receiver(post_delete, sender=ProfileTokenRange)
def touch_profile_on_token_range_change(sender, instance, **kwargs):
    _touch_routing_profile(instance.profile)


@receiver(post_save, sender=ProfilePersistentTier)
@receiver(post_delete, sender=ProfilePersistentTier)
def touch_profile_on_persistent_tier_change(sender, instance, **kwargs):
    profile = instance.token_range.profile if instance.token_range else None
    _touch_routing_profile(profile)


@receiver(post_save, sender=ProfilePersistentTierEndpoint)
@receiver(post_delete, sender=ProfilePersistentTierEndpoint)
def touch_profile_on_persistent_tier_endpoint_change(sender, instance, **kwargs):
    profile = instance.tier.token_range.profile if instance.tier and instance.tier.token_range else None
    _touch_routing_profile(profile)


@receiver(post_save, sender=ProfileBrowserTier)
@receiver(post_delete, sender=ProfileBrowserTier)
def touch_profile_on_browser_tier_change(sender, instance, **kwargs):
    _touch_routing_profile(instance.profile)


@receiver(post_save, sender=ProfileBrowserTierEndpoint)
@receiver(post_delete, sender=ProfileBrowserTierEndpoint)
def touch_profile_on_browser_tier_endpoint_change(sender, instance, **kwargs):
    profile = instance.tier.profile if instance.tier else None
    _touch_routing_profile(profile)


@receiver(post_save, sender=ProfileEmbeddingsTier)
@receiver(post_delete, sender=ProfileEmbeddingsTier)
def touch_profile_on_embeddings_tier_change(sender, instance, **kwargs):
    _touch_routing_profile(instance.profile)


@receiver(post_save, sender=ProfileEmbeddingsTierEndpoint)
@receiver(post_delete, sender=ProfileEmbeddingsTierEndpoint)
def touch_profile_on_embeddings_tier_endpoint_change(sender, instance, **kwargs):
    profile = instance.tier.profile if instance.tier else None
    _touch_routing_profile(profile)


@receiver(post_save, sender=MCPServerConfig)
@receiver(post_delete, sender=MCPServerConfig)
def invalidate_mcp_tool_cache_for_server(sender, instance, **kwargs):
    server_id = getattr(instance, "id", None)
    if server_id:
        invalidate_mcp_tool_cache(str(server_id))
        if getattr(instance, "scope", None) != MCPServerConfig.Scope.PLATFORM:
            from api.services.mcp_tool_discovery import schedule_mcp_tool_discovery

            schedule_mcp_tool_discovery(str(server_id), reason="config_changed")


@receiver(post_save, sender=MCPServerOAuthCredential)
@receiver(post_delete, sender=MCPServerOAuthCredential)
def invalidate_mcp_tool_cache_for_credentials(sender, instance, **kwargs):
    server_id = getattr(instance, "server_config_id", None)
    if server_id:
        invalidate_mcp_tool_cache(str(server_id))
        server = MCPServerConfig.objects.filter(id=server_id).only("scope").first()
        if server and server.scope != MCPServerConfig.Scope.PLATFORM:
            from api.services.mcp_tool_discovery import schedule_mcp_tool_discovery

            schedule_mcp_tool_discovery(str(server_id), reason="credentials_changed")
