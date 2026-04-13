from django.db import models
from django.utils import timezone
from django.db.models import F
from django.conf import settings
import random
import string


def _generate_unique_code(length=3):
    """Generate a unique alphanumeric code of given length."""
    chars = string.ascii_letters + string.digits
    while True:
        code = ''.join(random.choices(chars, k=length))
        if not LandingPage.objects.filter(code=code).exists():
            return code


class LandingPage(models.Model):
    """Custom landing page definition used for short URLs."""

    code = models.CharField(
        max_length=128,
        unique=True,
        blank=True,
        help_text="Unique code for the landing page, auto-generated if not provided."
    )
    charter = models.TextField()
    title = models.CharField(max_length=512, blank=True)
    hero_text = models.CharField(
        max_length=256,
        blank=True,
        help_text="Text displayed prominently on the landing page. Use {blue} and {/blue} to format text in blue."
    )
    image_url = models.URLField(blank=True)
    private_description = models.TextField(
        blank=True,
        help_text="Internal-only notes for this landing page."
    )
    utm_source = models.CharField(max_length=256, blank=True)
    utm_medium = models.CharField(max_length=256, blank=True)
    utm_campaign = models.CharField(max_length=256, blank=True)
    utm_term = models.CharField(max_length=256, blank=True)
    utm_content = models.CharField(max_length=256, blank=True)
    hits = models.PositiveIntegerField(default=0)
    disabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _generate_unique_code()
        super().save(*args, **kwargs)

    def increment_hits(self):
        """Increment hit counter atomically."""
        LandingPage.objects.filter(pk=self.pk).update(hits=F('hits') + 1)

    def __str__(self):
        return self.title or (self.charter[:50] + ('...' if len(self.charter) > 50 else ''))


class MiniModeCampaignPattern(models.Model):
    """Pattern used to enable mini mode when matched against utm_campaign."""

    pattern = models.CharField(
        max_length=256,
        unique=True,
        help_text=(
            "Case-insensitive pattern for utm_campaign. "
            "Use '*' as a wildcard (examples: agents_202602, c-*, bigcampaign)."
        ),
    )
    is_active = models.BooleanField(default=True)
    notes = models.CharField(
        max_length=512,
        blank=True,
        help_text="Optional internal notes about when to use this pattern.",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("pattern",)
        verbose_name = "Mini mode campaign pattern"
        verbose_name_plural = "Mini mode campaign patterns"

    def save(self, *args, **kwargs):
        # Store normalized patterns so matching is deterministic.
        self.pattern = (self.pattern or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.pattern


class ImmutableCallToActionSlugError(ValueError):
    """Raised when attempting to change a CTA slug after creation."""


class CallToAction(models.Model):
    """Stable CTA identity whose text evolves through append-only versions."""

    slug = models.SlugField(
        max_length=128,
        unique=True,
        help_text="Stable identifier used in templates, for example 'cta_homepage_main'.",
    )
    description = models.TextField(
        help_text="Human-readable explanation of where this CTA is used.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("slug",)
        verbose_name = "CTA"
        verbose_name_plural = "CTAs"

    def __str__(self):
        return self.slug

    @property
    def current_version(self):
        if not self.pk:
            return None
        return self.versions.first()

    @property
    def current_text(self) -> str:
        version = self.current_version
        return version.text if version else ""

    def add_version(self, text: str, *, created_by=None):
        normalized_text = (text or "").strip()
        if not normalized_text:
            raise ValueError("CTA text cannot be blank.")
        if not self.pk:
            raise ValueError("CTA must be saved before adding versions.")
        return self.versions.create(text=normalized_text, created_by=created_by)

    def save(self, *args, **kwargs):
        if self.pk:
            existing_slug = type(self).objects.filter(pk=self.pk).values_list("slug", flat=True).first()
            if existing_slug and existing_slug != self.slug:
                raise ImmutableCallToActionSlugError("CTA slugs are immutable once created.")
        super().save(*args, **kwargs)


class CallToActionVersion(models.Model):
    """A historical CTA text revision. The newest row is the active version."""

    cta = models.ForeignKey(
        CallToAction,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    text = models.TextField(
        help_text="The customer-facing CTA text rendered in templates.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_cta_versions",
        help_text="Admin user who created this CTA version.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "CTA version"
        verbose_name_plural = "CTA versions"

    def __str__(self):
        timestamp = self.created_at.isoformat() if self.created_at else "unsaved"
        return f"{self.cta.slug} @ {timestamp}"

    def save(self, *args, **kwargs):
        self.text = (self.text or "").strip()
        if not self.text:
            raise ValueError("CTA version text cannot be blank.")
        super().save(*args, **kwargs)
