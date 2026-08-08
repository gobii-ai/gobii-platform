import json

from bs4 import BeautifulSoup
from django import template
from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase, tag


@tag("batch_pages_signals")
class LucideIconTemplateTagTests(SimpleTestCase):
    def _render_icon(self, template_source, context=None):
        rendered = Template(
            f"{{% load lucide_icons %}}{template_source}"
        ).render(Context(context or {}))
        return BeautifulSoup(rendered, "html.parser").find("svg")

    def test_renders_decorative_icon_server_side(self):
        icon = self._render_icon(
            '{% lucide "users" class="h-4 w-4 text-violet-500" %}'
        )

        self.assertIsNotNone(icon)
        self.assertEqual(
            set(icon["class"]),
            {"lucide", "lucide-users", "h-4", "w-4", "text-violet-500"},
        )
        self.assertEqual(icon["viewbox"], "0 0 24 24")
        self.assertEqual(icon["width"], "24")
        self.assertEqual(icon["height"], "24")
        self.assertEqual(icon["aria-hidden"], "true")
        self.assertEqual(icon["focusable"], "false")
        self.assertIsNone(icon.get("data-lucide"))
        self.assertTrue(icon.find_all(["circle", "path"]))

    def test_accepts_dynamic_name_label_and_stroke_width(self):
        icon = self._render_icon(
            '{% lucide icon_name class="size-5" label="Open settings" stroke_width="1.5" %}',
            {"icon_name": "settings-2"},
        )

        self.assertIn("lucide-settings-2", icon["class"])
        self.assertEqual(icon["aria-label"], "Open settings")
        self.assertEqual(icon["role"], "img")
        self.assertIsNone(icon.get("aria-hidden"))
        self.assertEqual(icon["stroke-width"], "1.5")

    def test_rejects_unknown_icon(self):
        with self.assertRaisesMessage(template.TemplateSyntaxError, "Unknown Lucide icon"):
            self._render_icon('{% lucide "not-a-real-lucide-icon" %}')

    def test_rejects_unsupported_options(self):
        with self.assertRaisesMessage(
            template.TemplateSyntaxError, "Unsupported lucide tag option"
        ):
            self._render_icon('{% lucide "users" onclick="alert(1)" %}')

    def test_catalog_matches_exact_frontend_dependency_version(self):
        package = json.loads(
            (settings.BASE_DIR / "frontend" / "package.json").read_text(encoding="utf-8")
        )
        catalog = json.loads(
            (settings.BASE_DIR / "vendor" / "lucide" / "icons.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            package["devDependencies"]["lucide-static"],
            catalog["version"],
        )
