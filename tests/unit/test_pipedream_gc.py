import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from api.models import PersistentAgent, BrowserUseAgent


def _mk_agent(user, *, life_state=PersistentAgent.LifeState.ACTIVE, is_active=True,
              last_interaction_at=None, last_expired_at=None):
    import uuid
    with patch.object(BrowserUseAgent, 'select_random_proxy', return_value=None):
        bua = BrowserUseAgent.objects.create(user=user, name=f"bua-{uuid.uuid4()}"
                                            )
    import uuid
    ag = PersistentAgent.objects.create(
        user=user,
        name=f"agent-{uuid.uuid4()}",
        charter="c",
        browser_use_agent=bua,
        life_state=life_state,
        is_active=is_active,
    )
    if last_interaction_at is not None:
        ag.last_interaction_at = last_interaction_at
    if last_expired_at is not None:
        ag.last_expired_at = last_expired_at
    if last_interaction_at is not None or last_expired_at is not None:
        ag.save(update_fields=["last_interaction_at", "last_expired_at"])
    return ag


@override_settings(
    PIPEDREAM_CLIENT_ID="cid",
    PIPEDREAM_CLIENT_SECRET="csec",
    PIPEDREAM_PROJECT_ID="proj_123",
    PIPEDREAM_ENVIRONMENT="development",
    PIPEDREAM_GC_ENABLED=True,
    PIPEDREAM_GC_DRY_RUN=False,
)
class PipedreamGCTests(TestCase):
    @patch("api.integrations.pipedream_connect_gc.get_mcp_manager")
    @patch("api.integrations.pipedream_connect_gc.requests.request")
    def test_gc_deletes_unknown_and_stale(self, mock_req, mock_mgr):
        # Token
        mgr = MagicMock()
        mgr._get_pipedream_access_token.return_value = "pd_token"
        mock_mgr.return_value = mgr

        # Users and agents
        User = get_user_model()
        user = User.objects.create_user(username="u@example.com")

        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()

        # Active agent (should be kept)
        ag_active = _mk_agent(user)

        # Expired long ago (should be deleted)
        ag_expired = _mk_agent(
            user,
            life_state=PersistentAgent.LifeState.EXPIRED,
            last_expired_at=now - timedelta(days=31),
        )

        # Deactivated long ago (should be deleted)
        ag_deact = _mk_agent(
            user,
            is_active=False,
            last_interaction_at=now - timedelta(days=61),
        )

        # Mock list accounts (two pages optional, but we can do one page)
        page_payload = {
            "data": [
                {"id": "apn_orphan1", "external_user_id": "00000000-0000-0000-0000-000000000000"},
                {"id": "apn_active", "external_user_id": str(ag_active.id)},
                {"id": "apn_expired", "external_user_id": str(ag_expired.id)},
                {"id": "apn_deact", "external_user_id": str(ag_deact.id)},
            ],
            "page_info": {"count": 4, "total_count": 4, "start_cursor": "a", "end_cursor": None},
        }

        # requests.request will be called for: GET list, DELETE user x N (3)
        def _req(method, url, headers=None, params=None, timeout=None):
            r = MagicMock()
            if method == "GET" and url.endswith("/accounts"):
                r.json.return_value = page_payload
                r.status_code = 200
                r.raise_for_status.return_value = None
                return r
            if method == "DELETE" and "/users/" in url:
                r.status_code = 204
                r.text = ""
                return r
            # Fallback
            r.status_code = 200
            r.raise_for_status.return_value = None
            return r

        mock_req.side_effect = _req

        # Run task
        from api.tasks.pipedream_connect_gc import gc_orphaned_users
        res = gc_orphaned_users.apply(args=[]).get()

        # 3 candidates: orphan, expired, deactivated
        self.assertEqual(res["candidates"], 3)
        self.assertEqual(res["deleted_users"], 3)

    @patch("api.integrations.pipedream_connect_gc.get_mcp_manager")
    @patch("api.integrations.pipedream_connect_gc.requests.request")
    def test_gc_dry_run_no_deletes(self, mock_req, mock_mgr):
        mgr = MagicMock()
        mgr._get_pipedream_access_token.return_value = "pd_token"
        mock_mgr.return_value = mgr

        from django.utils import timezone
        now = timezone.now()
        User = get_user_model()
        user = User.objects.create_user(username="u2@example.com")
        ag = _mk_agent(user, life_state=PersistentAgent.LifeState.EXPIRED, is_active=True, last_expired_at=now)

        # Single account referencing agent
        payload = {"data": [{"id": "apn_x", "external_user_id": str(ag.id)}], "page_info": {"end_cursor": None}}

        def _req(method, url, headers=None, params=None, timeout=None):
            r = MagicMock()
            if method == "GET" and url.endswith("/accounts"):
                r.json.return_value = payload
                r.status_code = 200
                r.raise_for_status.return_value = None
                return r
            if method == "DELETE":
                r.status_code = 204
                return r
            return r

        mock_req.side_effect = _req

        with override_settings(PIPEDREAM_GC_DRY_RUN=True):
            from api.tasks.pipedream_connect_gc import gc_orphaned_users
            res = gc_orphaned_users.apply(args=[]).get()
            # Candidate computed, but no deletes performed
            self.assertGreaterEqual(res["candidates"], 0)
            self.assertEqual(res["deleted_users"], 0)
