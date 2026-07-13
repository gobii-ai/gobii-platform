import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import redis
import requests
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase, tag
from django.utils import timezone

from api.models import MCPServerConfig, MCPServerOAuthCredential
from api.services.mcp_oauth import (
    MCPOAuthStatus,
    ensure_mcp_oauth_credential,
)


@tag("batch_mcp_tools")
class MCPOAuthRefreshTests(TestCase):
    def _create_credential(self, *, expires_at):
        user = get_user_model().objects.create_user(
            username=f"mcp-oauth-{uuid.uuid4().hex[:8]}",
        )
        config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=user,
            name=f"notion-{uuid.uuid4().hex[:8]}",
            display_name="Notion",
            url="https://notion.example.com/mcp",
            auth_method=MCPServerConfig.AuthMethod.OAUTH2,
        )
        credential = MCPServerOAuthCredential.objects.create(
            server_config=config,
            user=user,
            client_id="client-id",
            expires_at=expires_at,
            metadata={"token_endpoint": "https://notion.example.com/oauth/token"},
        )
        credential.client_secret = "client-secret"
        credential.access_token = "old-access"
        credential.refresh_token = "refresh-token"
        credential.save()
        return config, credential

    @staticmethod
    def _success_response():
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        return response

    @patch("api.services.mcp_oauth._load_config", side_effect=DatabaseError("offline"))
    def test_database_failure_is_temporarily_unavailable(self, _mock_load):
        result = ensure_mcp_oauth_credential(str(uuid.uuid4()))

        self.assertEqual(result.status, MCPOAuthStatus.TEMPORARILY_UNAVAILABLE)
        self.assertEqual(result.cache_state, "database_unavailable")
        self.assertIsNone(result.credential)

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    @patch("api.services.mcp_oauth.requests.post")
    def test_transient_refresh_failure_uses_still_valid_token_and_cools_down(
        self,
        mock_post,
        _mock_schedule,
    ):
        config, _credential = self._create_credential(
            expires_at=timezone.now() + timedelta(minutes=1),
        )
        mock_post.side_effect = requests.Timeout("timed out")

        first = ensure_mcp_oauth_credential(str(config.id))
        second = ensure_mcp_oauth_credential(str(config.id))

        self.assertEqual(first.status, MCPOAuthStatus.USABLE)
        self.assertEqual(first.cache_state, "refresh_failed_with_valid_token")
        self.assertEqual(second.status, MCPOAuthStatus.USABLE)
        self.assertEqual(second.cache_state, "failure_bypassed_with_valid_token")
        mock_post.assert_called_once()

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    @patch("api.services.mcp_oauth.requests.post")
    def test_invalid_grant_requires_reconnect_and_uses_failure_cache(
        self,
        mock_post,
        _mock_schedule,
    ):
        config, _credential = self._create_credential(
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        response = MagicMock(status_code=400)
        response.json.return_value = {"error": "invalid_grant"}
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        mock_post.return_value = response

        first = ensure_mcp_oauth_credential(str(config.id))
        second = ensure_mcp_oauth_credential(str(config.id))

        self.assertEqual(first.status, MCPOAuthStatus.RECONNECT_REQUIRED)
        self.assertEqual(second.status, MCPOAuthStatus.RECONNECT_REQUIRED)
        self.assertEqual(second.cache_state, "failure_hit")
        mock_post.assert_called_once()

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    @patch("api.services.mcp_oauth.requests.post")
    @patch("api.services.mcp_oauth.Redlock", side_effect=redis.exceptions.ConnectionError)
    def test_redis_failure_falls_back_to_local_refresh(
        self,
        _mock_redlock,
        mock_post,
        _mock_schedule,
    ):
        config, _credential = self._create_credential(
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        mock_post.return_value = self._success_response()

        result = ensure_mcp_oauth_credential(str(config.id))

        self.assertEqual(result.status, MCPOAuthStatus.USABLE)
        self.assertTrue(result.refreshed)
        mock_post.assert_called_once()

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    @patch("api.services.mcp_oauth.requests.post")
    @patch("api.services.mcp_oauth.Redlock")
    def test_lock_waiter_reloads_token_published_by_winner(
        self,
        mock_redlock,
        mock_post,
        _mock_schedule,
    ):
        config, credential = self._create_credential(
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        lock = mock_redlock.return_value

        def publish_and_timeout(*_args, **_kwargs):
            published_at = timezone.now() + timedelta(seconds=1)
            credential.access_token = "peer-access"
            credential.expires_at = timezone.now() + timedelta(hours=1)
            credential.updated_at = published_at
            credential.save(
                update_fields=["access_token_encrypted", "expires_at", "updated_at"]
            )
            return False

        lock.acquire.side_effect = publish_and_timeout

        result = ensure_mcp_oauth_credential(str(config.id))

        self.assertEqual(result.status, MCPOAuthStatus.USABLE)
        self.assertEqual(result.cache_state, "waited_for_refresh")
        self.assertEqual(result.credential.access_token, "peer-access")
        mock_post.assert_not_called()
        lock.release.assert_not_called()

    @patch("api.services.mcp_tool_discovery.schedule_mcp_tool_discovery")
    @patch("api.services.mcp_oauth.requests.post")
    def test_credential_revision_bypasses_prior_failure_marker(
        self,
        mock_post,
        _mock_schedule,
    ):
        config, credential = self._create_credential(
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        failed_response = MagicMock(status_code=400)
        failed_response.json.return_value = {"error": "invalid_grant"}
        failed_response.raise_for_status.side_effect = requests.HTTPError(
            response=failed_response
        )
        mock_post.return_value = failed_response
        self.assertEqual(
            ensure_mcp_oauth_credential(str(config.id)).status,
            MCPOAuthStatus.RECONNECT_REQUIRED,
        )

        credential.refresh_from_db()
        credential.refresh_token = "replacement-refresh"
        credential.updated_at = timezone.now() + timedelta(seconds=1)
        credential.save(update_fields=["refresh_token_encrypted", "updated_at"])
        mock_post.reset_mock()
        mock_post.return_value = self._success_response()

        result = ensure_mcp_oauth_credential(str(config.id))

        self.assertEqual(result.status, MCPOAuthStatus.USABLE)
        self.assertTrue(result.refreshed)
        mock_post.assert_called_once()
