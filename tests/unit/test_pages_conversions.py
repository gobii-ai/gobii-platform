import hashlib
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings, tag
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils import timezone

from pages.conversions import (
    ConversionEvent,
    build_conversion_event,
    build_facebook_payload,
    build_reddit_payload,
)
from pages.tasks import send_facebook_signup_conversion, send_reddit_signup_conversion
from pages.context_processors import analytics as analytics_context, show_signup_tracking as tracking_context
from constants.plans import PlanNames


@tag('batch_marketing_conversions')
class ConversionPayloadTests(SimpleTestCase):
    def test_build_facebook_payload_hashes_personal_fields(self):
        event = ConversionEvent(
            event_name='SignUp',
            event_time=1_700_000_000,
            event_id='signup-123',
            email='USER@example.com',
            first_name='Jane',
            last_name='Doe',
            phone_number='+1 (555) 123-4567',
            external_id='42',
            ip_address='203.0.113.5',
            user_agent='pytest-agent/1.0',
            event_source_url='https://example.com/signup',
            fbc='fb.1.1234567890.AbCd',
            fbp='fb.1.1700000000.111111',
            fbclid='fbclid-123',
            custom_data={'plan': PlanNames.FREE},
            campaign={'source': 'newsletter'},
            value=0.0,
            currency='USD',
        )

        payload = build_facebook_payload(event, pixel_id='1234567890')

        self.assertIsNotNone(payload)
        self.assertIn('data', payload)

        fb_event = payload['data'][0]
        self.assertEqual(fb_event['event_name'], 'SignUp')
        self.assertEqual(fb_event['event_id'], 'signup-123')
        self.assertEqual(fb_event['event_time'], 1_700_000_000)

        user_data = fb_event['user_data']
        email_hash = hashlib.sha256('user@example.com'.encode('utf-8')).hexdigest()
        self.assertEqual(user_data['em'][0], email_hash)

        phone_hash = hashlib.sha256('15551234567'.encode('utf-8')).hexdigest()
        self.assertEqual(user_data['ph'][0], phone_hash)

        self.assertEqual(user_data['client_ip_address'], '203.0.113.5')
        self.assertEqual(user_data['client_user_agent'], 'pytest-agent/1.0')
        self.assertEqual(user_data['fbc'], 'fb.1.1234567890.AbCd')
        self.assertEqual(user_data['fbp'], 'fb.1.1700000000.111111')
        self.assertEqual(user_data['subscription_id'], 'fbclid-123')

        self.assertIn('custom_data', fb_event)
        self.assertEqual(fb_event['custom_data']['plan'], PlanNames.FREE)
        self.assertEqual(fb_event['custom_data']['source'], 'newsletter')
        self.assertEqual(fb_event['custom_data']['currency'], 'USD')
        self.assertEqual(fb_event['custom_data']['value'], 0.0)

    def test_build_reddit_payload_combines_custom_data(self):
        event = ConversionEvent(
            event_name='SignUp',
            event_time=1_700_000_000,
            event_id='signup-123',
            email='user@example.com',
            external_id='42',
            ip_address='198.51.100.9',
            user_agent='pytest-agent/1.0',
            click_ids={'click_id': 'rdt-123'},
            custom_data={'plan': PlanNames.FREE},
            campaign={'utm_source': 'reddit'},
        )

        payload = build_reddit_payload(event, advertiser_id='adv-123')

        self.assertIsNotNone(payload)
        self.assertEqual(payload['advertiser_id'], 'adv-123')
        reddit_event = payload['events'][0]

        email_hash = hashlib.sha256('user@example.com'.encode('utf-8')).hexdigest()
        self.assertEqual(reddit_event['user']['email'], email_hash)
        self.assertEqual(reddit_event['user']['ip_address'], '198.51.100.9')
        self.assertEqual(reddit_event['context']['click_id'], 'rdt-123')
        self.assertEqual(reddit_event['custom_data']['plan'], PlanNames.FREE)
        self.assertEqual(reddit_event['custom_data']['utm_source'], 'reddit')

    def test_build_conversion_event_accepts_datetime(self):
        event_time = timezone.now()
        payload = {
            'event_name': 'SignUp',
            'event_id': 'signup-789',
            'event_time': event_time,
            'email': 'user@example.com',
        }

        event = build_conversion_event(payload)

        self.assertEqual(event.event_name, 'SignUp')
        self.assertEqual(event.event_id, 'signup-789')
        self.assertEqual(event.event_time, int(event_time.timestamp()))
        self.assertEqual(event.email, 'user@example.com')


@tag('batch_marketing_conversions')
class ConversionTaskGuardsTests(SimpleTestCase):
    payload = {
        'event_name': 'SignUp',
        'event_id': 'signup-guard',
        'event_time': 1_700_000_001,
        'email': 'user@example.com',
    }

    @override_settings(
        GOBII_PROPRIETARY_MODE=False,
        FACEBOOK_PIXEL_ID='123',
        FACEBOOK_ACCESS_TOKEN='token',
    )
    def test_facebook_task_noop_when_not_proprietary(self):
        with patch('pages.tasks._post_json') as mock_post:
            send_facebook_signup_conversion.run(self.payload)

        mock_post.assert_not_called()

    @override_settings(
        GOBII_PROPRIETARY_MODE=False,
        REDDIT_ADVERTISER_ID='adv-123',
        REDDIT_ACCESS_TOKEN='token',
    )
    def test_reddit_task_noop_when_not_proprietary(self):
        with patch('pages.tasks._post_json') as mock_post:
            send_reddit_signup_conversion.run(self.payload)

        mock_post.assert_not_called()


@tag('batch_marketing_conversions')
class SignupTrackingContextTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _with_session(self):
        request = self.factory.get('/')
        request.session = {}
        request.user = AnonymousUser()
        return request

    def test_context_uses_session_values(self):
        request = self._with_session()
        request.session['show_signup_tracking'] = True
        request.session['signup_event_id'] = 'reg-123'
        request.session['signup_user_id'] = '42'
        request.session['signup_email_hash'] = 'abc123'
        tracking = tracking_context(request)
        self.assertTrue(tracking['show_signup_tracking'])
        self.assertEqual(tracking['signup_event_id'], 'reg-123')
        self.assertEqual(tracking['signup_user_id'], '42')
        self.assertEqual(tracking['signup_email_hash'], 'abc123')

        analytics = analytics_context(request)
        self.assertEqual(analytics['analytics']['data']['email_hash'], 'abc123')
