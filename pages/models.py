from django.db import models
from django.utils import timezone
from django.db.models import F
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
