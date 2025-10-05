
from django.contrib import admin
from django.contrib.sites.models import Site
from django.utils.html import format_html

from .models import LandingPage
from django.urls import reverse


@admin.register(LandingPage)
class LandingPageAdmin(admin.ModelAdmin):
    list_display = ("code", "url", "title", "hits", "disabled")
    readonly_fields = ("hits", "created_at", "updated_at")
    search_fields = ("code", "title", "charter", "private_description")
    list_filter = ("disabled",)
    fieldsets = (
        (None, {
            "fields": ("code", "title", "hero_text", "charter", "image_url", "disabled"),
        }),
        ("Tracking", {
            "fields": ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"),
        }),
        ("Internal", {
            "fields": ("private_description",),
        }),
        ("Metrics", {
            "fields": ("hits", "created_at", "updated_at"),
        }),
    )

    def __init__(self, model, admin_site):
        self.request = None
        super().__init__(model, admin_site)

    def get_queryset(self, request):
        self.request = request
        return super().get_queryset(request)

    @admin.display(description="URL")
    def url(self, obj):
        """Generate the URL for the landing page."""
        rel =  reverse('pages:landing_redirect', kwargs={'code': obj.code})
        current_site = Site.objects.get_current()

        # get if https from request
        protocol = 'https://' if self.request.is_secure() else 'http://'

        # Ensure the site domain is used to create the absolute URL
        absolute_url = f"{protocol}{current_site.domain}{rel}"

        return format_html(f'<a href="{absolute_url}" target="_blank">{absolute_url}</a>')
