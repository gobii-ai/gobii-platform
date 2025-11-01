from django.test import SimpleTestCase, tag

from marketing_events.schema import normalize_event


@tag("batch_marketing_events")
class NormalizeEventTests(SimpleTestCase):
    def test_normalize_event_hashes_and_defaults(self):
        payload = {
            "event_name": "CompleteRegistration",
            "properties": {"foo": "bar"},
            "user": {
                "id": "123",
                "email": "TEST@EXAMPLE.COM",
                "phone": " 555-1212 ",
            },
            "context": {
                "client_ip": "1.2.3.4",
                "user_agent": "UA",
                "page": {"url": "https://x"},
                "click_ids": {"fbp": "fbp"},
            },
        }
        out = normalize_event(payload)
        self.assertEqual(out["event_name"], "CompleteRegistration")
        self.assertTrue(out["ids"]["em"])
        self.assertTrue(out["ids"]["external_id"])
