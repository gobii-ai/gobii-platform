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
        self.assertIn("ETag", response)

    def test_app_shell_returns_304_for_matching_etag(self):
        initial_response = self.client.get("/app")
        response = self.client.get("/app", HTTP_IF_NONE_MATCH=initial_response["ETag"])

        self.assertEqual(response.status_code, 304)
        self.assertEqual(response["Cache-Control"], "no-cache, must-revalidate")
        self.assertEqual(response["ETag"], initial_response["ETag"])


@tag("batch_pages")
class AppShellAuthenticationTests(TestCase):
    def test_unauthenticated_agents_index_redirects_to_login(self):
        response = self.client.get("/app/agents/")

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))
        self.assertEqual(parse_qs(parsed.query), {"next": ["/app/agents/"]})

    def test_unauthenticated_new_agent_redirects_to_login(self):
        response = self.client.get("/app/agents/new")

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))
        self.assertEqual(parse_qs(parsed.query), {"next": ["/app/agents/new"]})

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

    def test_unauthenticated_app_root_still_serves_shell(self):
        response = self.client.get("/app")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-cache, must-revalidate")
        self.assertContains(response, 'id="gobii-frontend-root"')

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
