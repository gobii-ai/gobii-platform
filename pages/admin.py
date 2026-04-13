from django.contrib import admin
from django.contrib.sites.models import Site
from django.utils.html import format_html

from django.urls import reverse

from .admin_forms import CallToActionAdminForm
from .models import CallToAction, CallToActionVersion, LandingPage, MiniModeCampaignPattern


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

        return format_html(
            '<a href="{}" target="_blank">{}</a>',
            absolute_url,
            absolute_url,
        )


@admin.register(MiniModeCampaignPattern)
class MiniModeCampaignPatternAdmin(admin.ModelAdmin):
    list_display = ("pattern", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("pattern", "notes")
    readonly_fields = ("created_at", "updated_at")
    fields = ("pattern", "is_active", "notes", "created_at", "updated_at")


class CallToActionVersionInline(admin.TabularInline):
    model = CallToActionVersion
    extra = 0
    can_delete = False
    fields = ("created_at", "created_by", "text_display")
    readonly_fields = ("created_at", "created_by", "text_display")
    verbose_name = "CTA version"
    verbose_name_plural = "CTA version history"

    @admin.display(description="Text")
    def text_display(self, obj):
        return format_html(
            '<div style="max-width: 48rem; white-space: pre-wrap;">{}</div>',
            obj.text,
        )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CallToAction)
class CallToActionAdmin(admin.ModelAdmin):
    form = CallToActionAdminForm
    inlines = (CallToActionVersionInline,)
    list_display = ("slug", "description_preview", "current_text_preview", "updated_at")
    search_fields = ("slug", "description")
    readonly_fields = ("current_text_display", "created_at", "updated_at")

    def get_inline_instances(self, request, obj=None):
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

    def get_fields(self, request, obj=None):
        fields = ["slug", "description", "new_text"]
        if obj is not None:
            fields.append("current_text_display")
        fields.extend(["created_at", "updated_at"])
        return fields

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly_fields.append("slug")
        return tuple(readonly_fields)

    @admin.display(description="Description")
    def description_preview(self, obj):
        if len(obj.description) <= 80:
            return obj.description
        return f"{obj.description[:77]}..."

    @admin.display(description="Current text")
    def current_text_preview(self, obj):
        current_text = obj.current_text
        if len(current_text) <= 80:
            return current_text
        return f"{current_text[:77]}..."

    @admin.display(description="Current text")
    def current_text_display(self, obj):
        if not obj.current_text:
            return "No active text yet."
        return format_html(
            '<div style="max-width: 48rem; white-space: pre-wrap;">{}</div>',
            obj.current_text,
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        new_text = form.cleaned_data.get("new_text")
        if new_text:
            obj.add_version(new_text, created_by=request.user)
