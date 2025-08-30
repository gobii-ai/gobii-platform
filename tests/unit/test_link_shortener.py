from django.test import TestCase

# Provide a minimal stub of ``urlextract`` so the sms sender module can be
# imported in environments where the third‑party library is unavailable.
import sys
import types
import re

from urlextract import URLExtract

from api.agent.tools.sms_sender import (
    ensure_scheme,
    create_shortened_link,
    shorten_links_in_body,
)
from api.models import LinkShortener
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site

from util.analytics import Analytics, AnalyticsEvent

User = get_user_model()


class EnsureSchemeTests(TestCase):
    """Unit tests for the ensure_scheme() helper."""

    def test_leaves_complete_url_unchanged(self):
        self.assertEqual(
            ensure_scheme("https://example.com/path"),
            "https://example.com/path",
        )

    def test_adds_scheme_to_bare_domain(self):
        self.assertEqual(
            ensure_scheme("example.com"),
            "https://example.com",
        )

    def test_handles_www_prefix(self):
        self.assertEqual(
            ensure_scheme("www.example.com"),
            "https://www.example.com",
        )

    def test_handles_protocol_relative(self):
        self.assertEqual(
            ensure_scheme("//cdn.example.com/image.png"),
            "https://cdn.example.com/image.png",
        )


class LinkShortenerTests(TestCase):
    """Integration tests around create_shortened_link()."""

    @classmethod
    def setUpTestData(cls):
        # Make sure the Sites framework has something to return
        Site.objects.update_or_create(
            id=1,
            defaults={"domain": "testserver", "name": "testserver"},
        )
        cls.user = User.objects.create_user("alice", "alice@example.com", "p@ssw0rd")

    @patch("util.analytics.Analytics.track_event")
    def test_link_is_canonicalised_before_save(self, mock_track):
        """`example.com` should be stored as `https://example.com`."""
        shortened = create_shortened_link("example.com", user=self.user)

        self.assertEqual(shortened.url, "https://example.com")
        self.assertTrue(shortened.code)                       # code was generated
        self.assertTrue(LinkShortener.objects.filter(url="https://example.com").exists())

    @patch("util.analytics.Analytics.track_event")
    def test_link_without_user_still_saves(self, mock_track):
        shortened = create_shortened_link("https://weather.com")

        self.assertEqual(shortened.url, "https://weather.com")
        # Analytics shouldn’t be called when user is None
        mock_track.assert_not_called()

    def test_short_code_generated_and_redirects(self):
        link = LinkShortener.objects.create(url="https://example.com")
        self.assertIsNotNone(link.code)
        resp = self.client.get(f"/m/{link.code}/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://example.com")
        link.refresh_from_db()
        self.assertEqual(link.hits, 1)

    @patch("util.analytics.Analytics.track_event")
    def test_link_with_user_tracks_event(self, mock_track):
        shortened = create_shortened_link("https://example.com/page", user=self.user)

        mock_track.assert_called_once()
        kwargs = mock_track.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.SMS_SHORTENED_LINK_CREATED)


class ShortenLinksInBodyTests(TestCase):
    """Unit tests around shorten_links_in_body."""

    @classmethod
    def setUpTestData(cls):
        Site.objects.update_or_create(
            id=1,
            defaults={"domain": "testserver", "name": "testserver"},
        )
        cls.user = User.objects.create_user("bob", "bob@example.com", "p@ssw0rd")

    @patch("api.agent.tools.sms_sender.create_shortened_link")
    def test_trailing_period_not_part_of_url(self, mock_create):
        mock_create.return_value = types.SimpleNamespace(code="abc123")
        body = "Go here https://example.com/example.html."

        result = shorten_links_in_body(body, user=self.user)

        mock_create.assert_called_once_with("https://example.com/example.html", self.user)
        self.assertEqual(result, "Go here https://testserver/m/abc123/.")

    @patch("api.agent.tools.sms_sender.create_shortened_link")
    def test_duplicate_links_shortened_once(self, mock_create):
        mock_create.return_value = types.SimpleNamespace(code="zzz")
        body = "First https://example.com/path and again https://example.com/path"

        result = shorten_links_in_body(body, user=self.user)

        mock_create.assert_called_once_with("https://example.com/path", self.user)
        self.assertEqual(
            result,
            "First https://testserver/m/zzz/ and again https://testserver/m/zzz/",
        )
