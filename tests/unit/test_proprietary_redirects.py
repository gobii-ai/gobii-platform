from django.test import TestCase, override_settings, tag


@tag("batch_pages")
class ProprietaryRedirectTests(TestCase):
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_shirt_redirect_routes_to_home_with_utm(self):
        for path in ("/shirt", "/shirt/"):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    response["Location"],
                    "/?utm_source=shirt&utm_medium=clothing",
                )

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_shirt_redirect_is_proprietary_only(self):
        response = self.client.get("/shirt")

        self.assertEqual(response.status_code, 404)
