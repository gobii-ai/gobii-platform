from django.template import Context, Template
from django.test import TestCase, tag

from pages.cta_service import get_cta_current_text, get_cta_text
from pages.models import CallToAction


@tag("batch_pages")
class CallToActionTemplateTagTests(TestCase):
    def test_cta_service_returns_latest_text_and_fallback(self):
        cta = CallToAction.objects.create(
            slug="cta_template_test",
            description="Template test CTA.",
        )
        cta.add_version("Old text")
        cta.add_version("Latest text")

        self.assertEqual(get_cta_current_text("cta_template_test"), "Latest text")
        self.assertEqual(get_cta_text("cta_missing", fallback="Fallback text"), "Fallback text")

    def test_cta_template_tag_uses_latest_text_and_fallback(self):
        cta = CallToAction.objects.create(
            slug="cta_template_render",
            description="Rendered CTA.",
        )
        cta.add_version("Start here")
        cta.add_version("Start now")

        rendered = Template(
            "{% load cta_tags %}"
            "{% cta_text 'cta_template_render' %}|"
            "{% cta_text 'cta_missing' fallback='Fallback text' %}"
        ).render(Context({}))

        self.assertEqual(rendered, "Start now|Fallback text")
