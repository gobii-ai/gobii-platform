from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, tag
from django.contrib.auth import get_user_model

from api import admin as api_admin  # noqa: F401
from api.models import MCPServerConfig


@tag("batch_mcp_admin")
class MCPServerConfigAdminCleanupTests(SimpleTestCase):
    def test_platform_mcp_config_is_not_registered_in_django_admin(self):
        self.assertFalse(admin.site.is_registered(MCPServerConfig))


@tag("batch_mcp_admin")
class MCPServerConfigModelGuardTests(TestCase):
    def test_reserved_identifier_blocked_for_non_platform(self):
        owner = get_user_model().objects.create_user(
            username="owner2",
            email="owner2@example.com",
            password="password123",
        )

        config = MCPServerConfig(
            scope=MCPServerConfig.Scope.USER,
            user=owner,
            name="pipedream",
            display_name="Pipedream",
            url="https://example.com/mcp",
        )

        with self.assertRaises(ValidationError):
            config.clean()
