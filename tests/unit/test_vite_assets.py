import json
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag
from django.urls import reverse

from config.vite import ViteAssetReleaseNotFound, clear_manifest_cache, get_vite_asset


@tag("batch_pages")
class ViteAssetResolutionTests(SimpleTestCase):
    def tearDown(self):
        clear_manifest_cache()
        super().tearDown()

    def _write_manifest(self, root: Path) -> Path:
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "src/main.tsx": {
                        "file": "assets/main-abc123.js",
                        "src": "src/main.tsx",
                        "isEntry": True,
                        "css": ["assets/main-def456.css"],
                    }
                }
            ),
            encoding="utf-8",
        )
        return manifest_path

    def test_uses_local_static_urls_when_shared_origin_is_not_configured(self):
        with TemporaryDirectory() as temp_dir:
            manifest_path = self._write_manifest(Path(temp_dir))
            with override_settings(
                VITE_USE_DEV_SERVER=False,
                VITE_MANIFEST_PATH=manifest_path,
                VITE_ASSET_BASE_URL="",
                STATIC_URL="/static/",
            ):
                clear_manifest_cache()
                asset = get_vite_asset("src/main.tsx")

        self.assertEqual(asset.scripts, ("/static/frontend/assets/main-abc123.js",))
        self.assertEqual(asset.styles, ("/static/frontend/assets/main-def456.css",))

    def test_uses_shared_origin_urls_when_release_id_is_available(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = self._write_manifest(temp_root)
            release_path = temp_root / ".git-commit"
            release_path.write_text("abc123def456\n", encoding="utf-8")
            with override_settings(
                VITE_USE_DEV_SERVER=False,
                VITE_MANIFEST_PATH=manifest_path,
                VITE_ASSET_BASE_URL="https://static.gobii.ai/frontend/releases",
                VITE_ASSET_RELEASE_ID="",
                VITE_ASSET_RELEASE_ID_FILE=release_path,
            ):
                clear_manifest_cache()
                asset = get_vite_asset("src/main.tsx")

        self.assertEqual(
            asset.scripts,
            ("https://static.gobii.ai/frontend/releases/abc123def456/assets/main-abc123.js",),
        )
        self.assertEqual(
            asset.styles,
            ("https://static.gobii.ai/frontend/releases/abc123def456/assets/main-def456.css",),
        )

    def test_raises_when_shared_origin_is_configured_without_release_id(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = self._write_manifest(temp_root)
            with override_settings(
                DEBUG=False,
                VITE_USE_DEV_SERVER=False,
                VITE_MANIFEST_PATH=manifest_path,
                VITE_ASSET_BASE_URL="https://static.gobii.ai/frontend/releases",
                VITE_ASSET_RELEASE_ID="",
                VITE_ASSET_RELEASE_ID_FILE=temp_root / ".git-commit",
            ):
                clear_manifest_cache()
                with self.assertRaisesMessage(ViteAssetReleaseNotFound, "Vite asset release ID is required"):
                    get_vite_asset("src/main.tsx")

    def test_raises_when_release_id_uses_unknown_placeholder(self):
        with TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = self._write_manifest(temp_root)
            release_path = temp_root / ".git-commit"
            release_path.write_text("unknown\n", encoding="utf-8")
            with override_settings(
                DEBUG=False,
                VITE_USE_DEV_SERVER=False,
                VITE_MANIFEST_PATH=manifest_path,
                VITE_ASSET_BASE_URL="https://static.gobii.ai/frontend/releases",
                VITE_ASSET_RELEASE_ID="",
                VITE_ASSET_RELEASE_ID_FILE=release_path,
            ):
                clear_manifest_cache()
                with self.assertRaisesMessage(ViteAssetReleaseNotFound, "Vite asset release ID is required"):
                    get_vite_asset("src/main.tsx")


@tag("batch_pages")
class AppShellCacheHeaderTests(TestCase):
    def test_app_shell_uses_revalidation_based_cache_headers(self):
        response = self.client.get("/app")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-cache, must-revalidate")
        self.assertEqual(response["X-Robots-Tag"], "noindex, follow")
        self.assertIn("ETag", response)
        self.assertContains(response, '<meta name="robots" content="noindex, follow">')

    def test_app_shell_returns_304_for_matching_etag(self):
        initial_response = self.client.get("/app")
        response = self.client.get("/app", HTTP_IF_NONE_MATCH=initial_response["ETag"])

        self.assertEqual(response.status_code, 304)
        self.assertEqual(response["Cache-Control"], "no-cache, must-revalidate")
        self.assertEqual(response["X-Robots-Tag"], "noindex, follow")
        self.assertEqual(response["ETag"], initial_response["ETag"])


@tag("batch_pages")
class AppShellAuthenticationTests(TestCase):
    def test_unauthenticated_protected_paths_redirect_to_login(self):
        protected_paths = [
            "/app/agents/",
            "/app/agents/new",
            "/app/billing",
            "/app/api-keys",
            "/app/team",
            "/app/organization",
            "/app/profile",
            "/app/secrets",
            "/app/usage",
            "/app/integrations",
            f"/app/agents/{uuid.uuid4()}/settings",
            f"/app/agents/{uuid.uuid4()}/secrets",
            f"/app/agents/{uuid.uuid4()}/email",
            f"/app/agents/{uuid.uuid4()}/files",
        ]
        for path in protected_paths:
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 302)
                parsed = urlparse(response["Location"])
                self.assertEqual(parsed.path, reverse("account_login"))
                self.assertEqual(parse_qs(parsed.query), {"next": [path]})
                self.assertEqual(response["X-Robots-Tag"], "noindex, follow")

    def test_unauthenticated_agent_detail_redirects_to_login_with_query_string(self):
        agent_id = uuid.uuid4()
        response = self.client.get(f"/app/agents/{agent_id}/", {"return_to": "/console/agents/"})

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))
        self.assertEqual(
            parse_qs(parsed.query),
            {"next": [f"/app/agents/{agent_id}/?return_to=%2Fconsole%2Fagents%2F"]},
        )
        self.assertEqual(response["X-Robots-Tag"], "noindex, follow")

    def test_authenticated_legacy_organization_path_redirects_to_team(self):
        user = get_user_model().objects.create_user(username="team-route-user")
        self.client.force_login(user)
        context_id = str(uuid.uuid4())

        response = self.client.get(
            "/app/organization",
            {"context_type": "organization", "context_id": context_id},
        )

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, "/app/team")
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "context_type": ["organization"],
                "context_id": [context_id],
            },
        )
        self.assertEqual(response["X-Robots-Tag"], "noindex, follow")

    def test_unauthenticated_app_root_still_serves_shell(self):
        response = self.client.get("/app")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-cache, must-revalidate")
        self.assertEqual(response["X-Robots-Tag"], "noindex, follow")
        self.assertContains(response, 'id="gobii-frontend-root"')

    @override_settings(
        PIPEDREAM_CLIENT_ID="test-client-id",
        PIPEDREAM_CLIENT_SECRET="test-client-secret",
        PIPEDREAM_PROJECT_ID="test-project-id",
    )
    def test_app_root_includes_pipedream_urls_when_integrations_are_configured(self):
        response = self.client.get("/app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'data-pipedream-apps-url="{reverse("console-pipedream-apps")}"',
        )
        self.assertContains(
            response,
            f'data-pipedream-app-search-url="{reverse("console-pipedream-app-search")}"',
        )
        self.assertContains(
            response,
            f'data-native-integrations-url="{reverse("console-native-integration-list")}"',
        )

    def test_authenticated_agents_detail_serves_shell(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="appshell@example.com",
            email="appshell@example.com",
            password="testpass123",
        )
        self.client.force_login(user)

        response = self.client.get(f"/app/agents/{uuid.uuid4()}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-cache, must-revalidate")
        self.assertContains(response, 'id="gobii-frontend-root"')

    def test_authenticated_agent_subviews_serve_shell(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="appshell-settings@example.com",
            email="appshell-settings@example.com",
            password="testpass123",
        )
        self.client.force_login(user)

        for suffix in ("settings", "secrets", "email", "files"):
            with self.subTest(suffix=suffix):
                response = self.client.get(f"/app/agents/{uuid.uuid4()}/{suffix}")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Cache-Control"], "no-cache, must-revalidate")
                self.assertContains(response, 'id="gobii-frontend-root"')

    def test_authenticated_billing_serves_shell(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="appshell-billing@example.com",
            email="appshell-billing@example.com",
            password="testpass123",
        )
        self.client.force_login(user)

        response = self.client.get("/app/billing")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-cache, must-revalidate")
        self.assertContains(response, 'id="gobii-frontend-root"')

    def test_app_billing_context_query_updates_session(self):
        from api.models import Organization, OrganizationMembership

        User = get_user_model()
        user = User.objects.create_user(
            username="appshell-billing-org@example.com",
            email="appshell-billing-org@example.com",
            password="testpass123",
        )
        organization = Organization.objects.create(
            name="App Billing Org",
            slug="app-billing-org",
            created_by=user,
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.client.force_login(user)

        response = self.client.get(
            "/app/billing",
            {"context_type": "organization", "context_id": str(organization.id)},
        )

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertEqual(session.get("context_type"), "organization")
        self.assertEqual(session.get("context_id"), str(organization.id))
        self.assertEqual(session.get("context_name"), organization.name)

    def test_authenticated_api_keys_serves_shell(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="appshell-api-keys@example.com",
            email="appshell-api-keys@example.com",
            password="testpass123",
        )
        self.client.force_login(user)

        response = self.client.get("/app/api-keys")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-cache, must-revalidate")
        self.assertContains(response, 'id="gobii-frontend-root"')


@tag("batch_pages")
@override_settings(LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED=True)
class LegacyConsolePageRedirectTests(TestCase):
    def test_unauthenticated_console_page_redirects_to_login_for_app_target(self):
        response = self.client.get("/console/billing/", {"seats_success": "1"})

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))
        self.assertEqual(parse_qs(parsed.query), {"next": ["/app/billing?seats_success=1"]})

    def test_unauthenticated_organization_redirect_preserves_context_for_after_login(self):
        from api.models import Organization

        User = get_user_model()
        owner = User.objects.create_user(
            username="legacy-org-link-owner@example.com",
            email="legacy-org-link-owner@example.com",
            password="testpass123",
        )

        organization = Organization.objects.create(
            name="Legacy Org Link",
            slug="legacy-org-link",
            created_by=owner,
        )

        response = self.client.get(f"/console/organizations/{organization.id}/")

        self.assertEqual(response.status_code, 302)
        login_redirect = urlparse(response["Location"])
        self.assertEqual(login_redirect.path, reverse("account_login"))
        next_url = parse_qs(login_redirect.query).get("next", [""])[0]
        app_redirect = urlparse(next_url)
        self.assertEqual(app_redirect.path, "/app/team")
        self.assertEqual(parse_qs(app_redirect.query), {
            "context_type": ["organization"],
            "context_id": [str(organization.id)],
        })

    @override_settings(LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED=False)
    def test_legacy_console_redirect_view_honors_disabled_setting(self):
        response = self.client.get("/console/billing/")

        self.assertEqual(response.status_code, 404)

    def test_authenticated_console_pages_redirect_to_app_equivalents(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="console-redirect@example.com",
            email="console-redirect@example.com",
            password="testpass123",
        )
        self.client.force_login(user)
        agent_id = uuid.uuid4()

        cases = {
            "/console/": "/app",
            "/console/agents/": "/app/agents",
            f"/console/agents/{agent_id}/": f"/app/agents/{agent_id}/settings",
            f"/console/agents/{agent_id}/chat/": f"/app/agents/{agent_id}",
            f"/console/agents/{agent_id}/chat/settings/": f"/app/agents/{agent_id}/settings",
            f"/console/agents/{agent_id}/chat/secrets/": f"/app/agents/{agent_id}/secrets",
            f"/console/agents/{agent_id}/email/": f"/app/agents/{agent_id}/email",
            f"/console/agents/{agent_id}/secrets/request/": f"/app/agents/{agent_id}/secrets/request",
            f"/console/agents/{agent_id}/secrets/request/remove/": f"/app/agents/{agent_id}/secrets/request",
            "/console/profile/": "/app/profile",
            "/console/usage/": "/app/usage",
            "/console/api-keys/": "/app/api-keys",
            "/console/secrets/": "/app/secrets",
            "/console/organizations/": "/app/team",
            "/console/organizations/add/": "/app/team",
        }
        for source, target in cases.items():
            with self.subTest(source=source):
                response = self.client.get(source)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], target)

    def test_console_billing_org_redirect_sets_context_and_preserves_query(self):
        from api.models import Organization, OrganizationMembership

        User = get_user_model()
        user = User.objects.create_user(
            username="console-billing-org@example.com",
            email="console-billing-org@example.com",
            password="testpass123",
        )
        organization = Organization.objects.create(
            name="Console Billing Org",
            slug="console-billing-org",
            created_by=user,
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.client.force_login(user)

        response = self.client.get(
            "/console/billing/",
            {"org_id": str(organization.id), "seats_success": "1"},
        )

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, "/app/billing")
        query = parse_qs(parsed.query)
        self.assertEqual(query.get("context_type"), ["organization"])
        self.assertEqual(query.get("context_id"), [str(organization.id)])
        self.assertEqual(query.get("org_id"), [str(organization.id)])
        self.assertEqual(query.get("seats_success"), ["1"])
        session = self.client.session
        self.assertEqual(session.get("context_type"), "organization")
        self.assertEqual(session.get("context_id"), str(organization.id))
