from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, tag
from django.contrib.auth import get_user_model

from api.admin import MCPServerConfigAdmin
from api.admin_forms import MCPServerConfigAdminForm
from api.models import MCPServerConfig, Organization


@tag("batch_mcp_admin")
class MCPServerConfigAdminTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="superuser",
            email="superuser@example.com",
            password="password123",
        )
        self.site = AdminSite()

    def _base_defaults(self):
        return {
            "display_name": "Bright Data",
            "description": "Web scraping",
            "command": "npx",
            "command_args": [],
            "url": "",
            "auth_method": MCPServerConfig.AuthMethod.NONE,
            "prefetch_apps": [],
            "metadata": {},
            "is_active": True,
        }

    def test_queryset_filters_to_platform_scope(self):
        platform_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="platform_only",
            **self._base_defaults(),
        )

        User = get_user_model()
        owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="password123",
        )
        org = Organization.objects.create(name="Org", slug="org", created_by=owner)

        org_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization=org,
            name="org_scope",
            **self._base_defaults(),
        )

        user_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=owner,
            name="user_scope",
            **self._base_defaults(),
        )

        request = self.factory.get("/admin/api/mcpserverconfig/")
        request.user = self.superuser

        admin_view = MCPServerConfigAdmin(MCPServerConfig, self.site)
        queryset = admin_view.get_queryset(request)
        queryset_ids = set(queryset.values_list("id", flat=True))

        self.assertIn(platform_config.id, queryset_ids)
        self.assertNotIn(org_config.id, queryset_ids)
        self.assertNotIn(user_config.id, queryset_ids)
        self.assertTrue(all(config.scope == MCPServerConfig.Scope.PLATFORM for config in queryset))

    def test_admin_form_can_create_platform_scoped_server(self):
        form_data = {
            "name": "platform_scraper",
            "display_name": "Platform Scraper",
            "description": "Runs a managed integration.",
            "command": "python",
            "command_args": '["-m", "scraper"]',
            "url": "",
            "auth_method": MCPServerConfig.AuthMethod.NONE,
            "prefetch_apps": "[]",
            "metadata": "{}",
            "is_active": "on",
            "environment": '{"API_TOKEN": "secret"}',
            "headers": '{"X-Auth": "1"}',
        }

        form = MCPServerConfigAdminForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)

        config = form.save()
        self.assertEqual(config.scope, MCPServerConfig.Scope.PLATFORM)
        self.assertIsNone(config.organization)
        self.assertIsNone(config.user)
        self.assertEqual(config.command_args, ["-m", "scraper"])
        self.assertEqual(config.environment, {"API_TOKEN": "secret"})
        self.assertEqual(config.headers, {"X-Auth": "1"})

    def test_admin_form_updates_environment_and_headers(self):
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="managed_scraper",
            **self._base_defaults(),
        )
        config.environment = {"API_TOKEN": "old"}
        config.headers = {"Authorization": "Bearer old"}
        config.save()

        form_data = {
            "name": "managed_scraper",
            "display_name": "Bright Data",
            "description": "Updated description",
            "command": "npx",
            "command_args": '["-y", "@brightdata/mcp@2.6.0"]',
            "url": "",
            "auth_method": MCPServerConfig.AuthMethod.BEARER_TOKEN,
            "prefetch_apps": '["sheets"]',
            "metadata": '{"env_fallback": {"API_TOKEN": "BRIGHT_DATA_TOKEN"}}',
            "is_active": "on",
            "environment": '{"API_TOKEN": "new"}',
            "headers": '{"X-Test": "value"}',
        }

        form = MCPServerConfigAdminForm(data=form_data, instance=config)
        self.assertTrue(form.is_valid(), form.errors)

        saved_config = form.save()
        self.assertEqual(saved_config.scope, MCPServerConfig.Scope.PLATFORM)
        self.assertIsNone(saved_config.organization)
        self.assertIsNone(saved_config.user)

        config.refresh_from_db()
        self.assertEqual(config.environment, {"API_TOKEN": "new"})
        self.assertEqual(config.headers, {"X-Test": "value"})
        self.assertEqual(config.command_args, ["-y", "@brightdata/mcp@2.6.0"])
        self.assertEqual(config.prefetch_apps, ["sheets"])
        self.assertEqual(config.auth_method, MCPServerConfig.AuthMethod.BEARER_TOKEN)

    def test_reserved_identifier_blocked_for_non_platform(self):
        owner = get_user_model().objects.create_user(
            username="owner2",
            email="owner2@example.com",
            password="password123",
        )

        cfg = MCPServerConfig(
            scope=MCPServerConfig.Scope.USER,
            user=owner,
            name="pipedream",
            **self._base_defaults(),
        )

        with self.assertRaises(ValidationError):
            cfg.clean()
