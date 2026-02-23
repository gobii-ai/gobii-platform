from django.test import TestCase, override_settings, tag


@tag("batch_pages")
class ApiDocsUrlTests(TestCase):
    _docs_paths = (
        "/api/schema/swagger-ui/",
        "/api/schema/redoc/",
        "/api/docs/",
    )
    _docs_redirect_url = "https://docs.gobii.ai/api-reference"

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_docs_urls_redirect_to_external_docs_in_proprietary_mode(self):
        for docs_path in self._docs_paths:
            with self.subTest(docs_path=docs_path):
                response = self.client.get(docs_path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self._docs_redirect_url)

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_docs_urls_use_local_views_in_community_mode(self):
        for docs_path in self._docs_paths:
            with self.subTest(docs_path=docs_path):
                response = self.client.get(docs_path)
                self.assertEqual(response.status_code, 200)


@tag("batch_pages")
class SiteDocsUrlTests(TestCase):
    _docs_paths = (
        "/docs/",
        "/docs/nonexistent-doc-page/",
    )
    _docs_redirect_url = "https://docs.gobii.ai/"

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_site_docs_urls_redirect_to_external_docs_in_proprietary_mode(self):
        for docs_path in self._docs_paths:
            with self.subTest(docs_path=docs_path):
                response = self.client.get(docs_path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self._docs_redirect_url)

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_docs_index_uses_existing_local_redirect_in_community_mode(self):
        response = self.client.get("/docs/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/docs/"))

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_docs_slug_uses_existing_local_markdown_view_in_community_mode(self):
        response = self.client.get("/docs/nonexistent-doc-page/")
        self.assertEqual(response.status_code, 404)
