import ipaddress
from datetime import datetime
from typing import Any, Optional

import segment.analytics as analytics
from enum import StrEnum
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.fields import DateTimeField

from observability import traced, trace

analytics.write_key = settings.SEGMENT_WRITE_KEY

import logging
logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

GOOGLE_TRUSTED = [
    ipaddress.ip_network("35.184.0.0/13"),  # your existing (keep if you know you use it)
    ipaddress.ip_network("35.191.0.0/16"),  # common Google Front End / LB
    ipaddress.ip_network("130.211.0.0/22"), # common Google Front End / LB
    ipaddress.ip_network("34.128.0.0/10"),
]

CLOUDFLARE_V4 = [
    ipaddress.ip_network("173.245.48.0/20"),
    ipaddress.ip_network("103.21.244.0/22"),
    ipaddress.ip_network("103.22.200.0/22"),
    ipaddress.ip_network("103.31.4.0/22"),
    ipaddress.ip_network("141.101.64.0/18"),
    ipaddress.ip_network("108.162.192.0/18"),
    ipaddress.ip_network("190.93.240.0/20"),
    ipaddress.ip_network("188.114.96.0/20"),
    ipaddress.ip_network("197.234.240.0/22"),
    ipaddress.ip_network("198.41.128.0/17"),
    ipaddress.ip_network("162.158.0.0/15"),
    ipaddress.ip_network("104.16.0.0/12"),  # covers 104.16.0.0 – 104.31.255.255
    ipaddress.ip_network("172.64.0.0/13"),
    ipaddress.ip_network("131.0.72.0/22"),
]

CLOUDFLARE_V6 = [
    ipaddress.ip_network("2400:cb00::/32"),
    ipaddress.ip_network("2606:4700::/32"),
    ipaddress.ip_network("2803:f800::/32"),
    ipaddress.ip_network("2405:b500::/32"),
    ipaddress.ip_network("2405:8100::/32"),
    ipaddress.ip_network("2a06:98c0::/29"),
    ipaddress.ip_network("2c0f:f248::/32"),
]

TRUSTED_PROXIES = GOOGLE_TRUSTED + CLOUDFLARE_V4 + CLOUDFLARE_V6

def _parse_ip(raw: Optional[str]) -> Optional[ipaddress._BaseAddress]:
    if not raw:
        return None
    try:
        return ipaddress.ip_address(raw.strip())
    except Exception:
        return None


def _is_trusted(ip: Optional[ipaddress._BaseAddress]) -> bool:
    if ip is None:
        return False
    return any(ip in net for net in TRUSTED_PROXIES)

class AnalyticsEvent(StrEnum):

    TASK_CREATED = 'Task Created'
    TASK_COMPLETED = 'Task Completed'
    TASK_FAILED = 'Task Failed'
    TASK_CANCELLED = 'Task Cancelled'
    TASK_PAUSED = 'Task Paused'
    TASK_RESUMED = 'Task Resumed'
    TASK_UPDATED = 'Task Updated'
    TASK_DELETED = 'Task Deleted'
    TASK_FETCHED = 'Task Fetched' 
    TASKS_LISTED = 'Tasks Listed'
    TASK_RESULT_VIEWED = 'Task Result Viewed'
    PING = 'Ping'
    AGENTS_LISTED = 'Agents Listed'
    AGENT_CREATED = 'Agent Created'
    AGENT_UPDATED = 'Agent Updated'
    AGENT_DELETED = 'Agent Deleted'

    # Web Analytics Events
    SIGNUP = 'Sign Up'
    LOGGED_IN = 'Log In'
    LOGGED_OUT = 'Log Out'
    SUPPORT_VIEW = 'Support View'
    PLAN_INTEREST = 'Paid Plan Interest'
    WEB_TASKS_LISTED = 'Tasks Listed'
    WEB_TASK_DETAILED = 'Task Details Viewed'
    WEB_TASK_RESULT_VIEWED = 'Task Result Viewed'
    WEB_TASK_RESULT_DOWNLOADED = 'Task Result Downloaded'
    WEB_TASK_CANCELLED = 'Task Cancelled'

    # Web Chat Events
    WEB_CHAT_SESSION_STARTED = 'Web Chat Session Started'
    WEB_CHAT_SESSION_ENDED = 'Web Chat Session Ended'
    WEB_CHAT_MESSAGE_SENT = 'Web Chat Message Sent'

    # Persistent Agent Events
    PERSISTENT_AGENT_CREATED = 'Persistent Agent Created'
    PERSISTENT_AGENT_UPDATED = 'Persistent Agent Updated'
    PERSISTENT_AGENT_DELETED = 'Persistent Agent Deleted'
    PERSISTENT_AGENT_VIEWED = 'Persistent Agent Viewed'
    PERSISTENT_AGENT_PROACTIVE_TRIGGERED = 'Persistent Agent Proactively Triggered'
    PERSISTENT_AGENT_SOFT_EXPIRED = 'Persistent Agent Soft Expired'
    PERSISTENT_AGENT_SOFT_LIMIT_EXCEEDED = 'Persistent Agent Soft Limit Exceeded'
    PERSISTENT_AGENT_HARD_LIMIT_EXCEEDED = 'Persistent Agent Hard Limit Exceeded'
    PERSISTENT_AGENT_BROWSER_DAILY_LIMIT_REACHED = 'Persistent Agent Browser Daily Limit Reached'
    PERSISTENT_AGENT_BURN_RATE_LIMIT_REACHED = 'Persistent Agent Burn Rate Limit Reached'
    PERSISTENT_AGENT_CAPTCHA_ATTEMPTED = 'Persistent Agent CAPTCHA Attempted'
    PERSISTENT_AGENT_CAPTCHA_SUCCEEDED = 'Persistent Agent CAPTCHA Succeeded'
    PERSISTENT_AGENT_CAPTCHA_FAILED = 'Persistent Agent CAPTCHA Failed'
    PERSISTENT_AGENT_SHUTDOWN = 'Persistent Agent Shutdown'
    PERSISTENT_AGENT_CHARTER_SUBMIT = 'Persistent Agent Charter Submitted'
    PERSISTENT_AGENTS_LISTED = 'Persistent Agents Listed'
    PERSISTENT_AGENT_EMAIL_SENT = 'Persistent Agent Message Sent'
    PERSISTENT_AGENT_EMAIL_RECEIVED = 'Persistent Agent Message Received'
    PERSISTENT_AGENT_EMAIL_OUT_OF_CREDITS = 'Persistent Agent Out of Credits Email'
    PERSISTENT_AGENT_DAILY_CREDIT_NOTICE_SENT = 'Persistent Agent Daily Credit Notice Sent'
    AGENT_FILE_SENT = 'Agent File Sent'
    AGENT_FILE_SEND_FAILED = 'Agent File Send Failed'
    AGENT_FILE_UNSUPPORTED = 'Agent File Unsupported'
    AGENT_FILES_VIEWED = 'Agent Files Viewed'
    AGENT_FILES_UPLOADED = 'Agent Files Uploaded'
    AGENT_FILES_UPLOAD_FAILED = 'Agent Files Upload Failed'
    AGENT_FILE_DOWNLOADED = 'Agent File Downloaded'
    AGENT_FILES_DELETED = 'Agent Files Deleted'
    AGENT_FOLDER_CREATED = 'Agent Folder Created'
    AGENT_FILE_MOVED = 'Agent File Moved'
    AGENT_FILE_EXPORTED = 'Agent File Exported'
    AGENT_ATTACHMENT_IMPORTED = 'Agent Attachment Imported'

    # PA Peer Links
    PERSISTENT_AGENT_PEER_LINKED = 'Persistent Agent Linked'
    PERSISTENT_AGENT_PEER_UNLINKED = 'Persistent Agent Unlinked'

    # PA MCP
    PERSISTENT_AGENT_MCP_LINKED = 'Persistent Agent MCP Linked'
    PERSISTENT_AGENT_MCP_UNLINKED = 'Persistent Agent MCP Unlinked'

    # PA Webhooks
    PERSISTENT_AGENT_WEBHOOK_ADDED = 'Persistent Agent Webhook Added'
    PERSISTENT_AGENT_WEBHOOK_UPDATED = 'Persistent Agent Webhook Updated'
    PERSISTENT_AGENT_WEBHOOK_DELETED = 'Persistent Agent Webhook Deleted'
    PERSISTENT_AGENT_WEBHOOK_TESTED = 'Persistent Agent Webhook Tested'
    PERSISTENT_AGENT_WEBHOOK_TRIGGERED = 'Persistent Agent Webhook Triggered'

    # SMS Events
    PERSISTENT_AGENT_SMS_SENT = 'Persistent Agent SMS Sent'
    PERSISTENT_AGENT_SMS_RECEIVED = 'Persistent Agent SMS Received'
    PERSISTENT_AGENT_SMS_DELIVERED = 'Persistent Agent SMS Delivered'
    PERSISTENT_AGENT_SMS_FAILED = 'Persistent Agent SMS Failed'

    # Persistent Agent Secrets Events
    PERSISTENT_AGENT_SECRETS_VIEWED = 'Persistent Agent Secrets Viewed'
    PERSISTENT_AGENT_SECRET_ADDED = 'Persistent Agent Secret Added'
    PERSISTENT_AGENT_SECRET_UPDATED = 'Persistent Agent Secret Updated'
    PERSISTENT_AGENT_SECRET_DELETED = 'Persistent Agent Secret Deleted'
    PERSISTENT_AGENT_SECRETS_PROVIDED = 'Persistent Agent Secrets Provided'
    
    # Contact Request Events
    AGENT_CONTACTS_REQUESTED = 'Agent Contacts Requested'
    AGENT_CONTACTS_APPROVED = 'Agent Contacts Approved'
    AGENT_CONTACTS_REJECTED = 'Agent Contacts Rejected'

    # Billing Events
    BILLING_CANCELLATION = 'Billing Cancellation'
    BILLING_UPDATED = 'Billing Updated'
    BILLING_VIEWED = 'Billing Viewed'
    BILLING_PAYMENT_FAILED = 'Billing Payment Failed'
    BILLING_PAYMENT_SUCCEEDED = 'Billing Payment Succeeded'

    # API Key Events
    API_KEY_CREATED = 'API Key Created'
    API_KEY_DELETED = 'API Key Deleted'
    API_KEY_REVOKED = 'API Key Revoked'

    # MCP Server Events
    MCP_SERVER_CREATED = 'MCP Server Created'
    MCP_SERVER_UPDATED = 'MCP Server Updated'
    MCP_SERVER_DELETED = 'MCP Server Deleted'

    # Console Events
    CONSOLE_HOME_VIEWED = 'Console Home Viewed'
    CONSOLE_USAGE_VIEWED = 'Console Usage Viewed'

    # Pipedream Events
    PIPEDREAM_JIT_CONNECT_REDIRECT = 'Pipedream JIT Connect Redirect'

    # Email Events
    EMAIL_OPENED = 'Email Opened'
    EMAIL_LINK_CLICKED = 'Email Link Clicked'

    # BYO Email – Account + Tests
    EMAIL_ACCOUNT_CREATED = 'Email Account Created'
    EMAIL_ACCOUNT_UPDATED = 'Email Account Updated'
    SMTP_TEST_PASSED = 'SMTP Test Passed'
    SMTP_TEST_FAILED = 'SMTP Test Failed'
    IMAP_TEST_PASSED = 'IMAP Test Passed'
    IMAP_TEST_FAILED = 'IMAP Test Failed'

    # Miscellaneous
    LANDING_PAGE_VISIT = 'Landing Page Visit'

    # Task Threshold Events
    TASK_THRESHOLD_REACHED = 'task_usage_threshold_reached'

    # Subscription Events
    SUBSCRIPTION_CREATED = 'Subscription Created'
    SUBSCRIPTION_UPDATED = 'Subscription Updated'
    SUBSCRIPTION_CANCELLED = 'Subscription Cancelled'
    SUBSCRIPTION_RENEWED = 'Subscription Renewed'

    # SMS Events
    SMS_VERIFICATION_CODE_SENT = 'SMS - Verification Code Sent'
    SMS_VERIFIED = 'SMS - Verified'
    SMS_DELETED = 'SMS - Deleted'
    SMS_RESEND_VERIFICATION_CODE = 'SMS - Resend Verification Code'
    SMS_SHORTENED_LINK_CREATED = 'SMS - Shortened Link Created'
    SMS_SHORTENED_LINK_DELETED = 'SMS - Shortened Link Deleted'
    SMS_SHORTENED_LINK_CLICKED = 'SMS - Shortened Link Clicked'

    # Organization Events
    ORGANIZATION_CREATED = 'Organization Created'
    ORGANIZATION_UPDATED = 'Organization Updated'
    ORGANIZATION_DELETED = 'Organization Deleted'
    ORGANIZATION_MEMBER_ADDED = 'Organization Member Added'
    ORGANIZATION_MEMBER_REMOVED = 'Organization Member Removed'
    ORGANIZATION_MEMBER_ROLE_UPDATED = 'Organization Member Role Updated'
    ORGANIZATION_BILLING_VIEWED = 'Organization Billing Viewed'
    ORGANIZATION_BILLING_UPDATED = 'Organization Billing Updated'
    ORGANIZATION_PLAN_CHANGED = 'Organization Plan Changed'
    ORGANIZATION_INVITE_SENT = 'Organization Invite Sent'
    ORGANIZATION_INVITE_ACCEPTED = 'Organization Invite Accepted'
    ORGANIZATION_INVITE_DECLINED = 'Organization Invite Declined'
    ORGANIZATION_AGENT_CREATED = 'Organization Agent Created'
    ORGANIZATION_AGENT_DELETED = 'Organization Agent Deleted'
    ORGANIZATION_TASK_CREATED = 'Organization Task Created'
    ORGANIZATION_TASK_DELETED = 'Organization Task Deleted'
    ORGANIZATION_TASKS_VIEWED = 'Organization Tasks Viewed'
    ORGANIZATION_API_KEY_CREATED = 'Organization API Key Created'
    ORGANIZATION_API_KEY_DELETED = 'Organization API Key Deleted'
    ORGANIZATION_API_KEY_REVOKED = 'Organization API Key Revoked'
    ORGANIZATION_PERSISTENT_AGENT_CREATED = 'Organization Persistent Agent Created'
    ORGANIZATION_PERSISTENT_AGENT_DELETED = 'Organization Persistent Agent Deleted'
    ORGANIZATION_SEAT_ADDED = 'Organization Seat Added'
    ORGANIZATION_SEAT_REMOVED = 'Organization Seat Removed'
    ORGANIZATION_SEAT_ASSIGNED = 'Organization Seat Assigned'
    ORGANIZATION_SEAT_UNASSIGNED = 'Organization Seat Unassigned'

class AnalyticsCTAs(StrEnum):
    CTA_CREATE_AGENT_CLICKED = 'CTA - Create Agent Clicked'
    CTA_EXAMPLE_AGENT_CLICKED = 'CTA - Example Agent Clicked'
    CTA_CREATE_AGENT_COMM_CLICKED = 'CTA - Create Agent Clicked - Comm Selected'
    CTA_CREATE_FIRST_AGENT_CLICKED = 'CTA - Create First Agent Clicked'

class AnalyticsSource(StrEnum):
    API = 'API'
    WEB = 'Web'
    NA = 'N/A'
    AGENT = 'Agent'
    EMAIL = 'Email'
    SMS = 'SMS'
    CONSOLE = 'Console'

class Analytics:
    @staticmethod
    def _is_analytics_enabled():
        return bool(settings.SEGMENT_WRITE_KEY)

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS Identify")
    def identify(user_id, traits):
        if Analytics._is_analytics_enabled():
            context = {
                'ip': '0',
            }

            if 'date_joined' in traits:
                try:
                    # Convert to unix timestamp if it's a datetime object
                    if isinstance(traits['date_joined'], datetime):
                        traits['date_joined'] = int(traits['date_joined'].timestamp())
                    elif not isinstance(traits['date_joined'], str):
                        traits['date_joined'] = ''
                except Exception as e:
                    del traits['date_joined']

            analytics.identify(user_id, traits, context)

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS Track")
    def track(user_id, event, properties, context: dict | None = None, ip: str = None, message_id: str = None, timestamp = None):
        context = context or {}
        if Analytics._is_analytics_enabled():
            with traced("ANALYTICS Track"):
                context['ip'] = '0'
                analytics.track(user_id, event, properties, context, timestamp, None, None, message_id)

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS Track Event")
    def track_event(user_id, event: AnalyticsEvent, source: AnalyticsSource, properties: dict | None = None, ip: str = None):
        properties = properties or {}
        if Analytics._is_analytics_enabled():
            with traced("ANALYTICS Track Event"):
                properties['medium'] = str(source)
                context = {
                    'ip': '0',
                }
                try:
                    analytics.track(user_id, event, properties, context)
                except Exception:
                    logger.exception(f"Failed to track event {event} for user {user_id}")

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS Track Event Anonymous")
    def track_event_anonymous(anonymous_id: str, event: AnalyticsEvent, source: AnalyticsSource, properties: dict | None = None, ip: str = None):
        """
        Track an event for an anonymous user. This is useful for tracking events that do not require user identification,
        such as page views or interactions that do not require authentication.

        Args:
            anonymous_id (str): The anonymous ID of the user. This should be a unique identifier for the user session.
            event (AnalyticsEvent): The event to track.
            source (AnalyticsSource): The source of the event, such as API or Web.
            properties (dict): A dictionary of properties to associate with the event.
            ip (str, optional): The IP address of the user. Defaults to None.
        """
        properties = properties or {}
        if Analytics._is_analytics_enabled():
            with traced("ANALYTICS Track Event Anonymous"):
                properties['medium'] = str(source)
                context = {
                    'ip': '0',
                }

                analytics.track(
                    anonymous_id=anonymous_id,
                    event=event,
                    properties=properties,
                    context=context
                )

    @staticmethod
    def with_org_properties(
        properties: dict | None = None,
        *,
        organization: object | None = None,
        organization_id: str | None = None,
        organization_name: str | None = None,
        organization_flag: bool | None = None,
    ) -> dict:
        """Return a copy of ``properties`` annotated with organization metadata.

        The helper accepts either an organization object (anything exposing ``id``/``name``),
        explicit identifiers, or a boolean flag to indicate whether the event occurred in an
        organization context.
        """

        props: dict[str, Any] = dict(properties or {})

        org = organization
        org_id_value = organization_id
        if org_id_value is None and org is not None:
            org_id_value = getattr(org, "id", None) or getattr(org, "pk", None)

        org_name_value = organization_name
        if org_name_value is None and org is not None:
            org_name_value = getattr(org, "name", None)

        if organization_flag is None:
            organization_flag = bool(org_id_value) or bool(org)

        props['organization'] = bool(organization_flag)

        if org_id_value:
            props['organization_id'] = str(org_id_value)

        if org_name_value:
            props['organization_name'] = org_name_value

        return props

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS Get Client IP")
    def get_client_ip(request) -> str:
        """
        Returns the most reliable client IP given Cloudflare -> GCLB -> GKE.

        Trust model:
          - Only honor forwarded headers if the *peer* (REMOTE_ADDR) is a trusted proxy.
          - Priority within trusted requests:
              1) True-Client-IP (Enterprise)
              2) CF-Connecting-IP
              3) Right-to-left scan of X-Forwarded-For removing trusted proxies
              4) Fallback to REMOTE_ADDR (diagnostic only; will be Google)
        """
        # 1) Who connected to us?
        peer_ip = _parse_ip(request.META.get("REMOTE_ADDR"))

        # If the peer isn't a trusted proxy, ignore headers and return the peer
        if not _is_trusted(peer_ip):
            return str(peer_ip) if peer_ip else '0'

        # 2) Prefer Cloudflare headers when peer is trusted
        for header in ("HTTP_TRUE_CLIENT_IP", "HTTP_CF_CONNECTING_IP"):
            hval = request.META.get(header)
            ip = _parse_ip(hval)
            if ip:
                return str(ip)

        # 3) Walk X-Forwarded-For right-to-left, dropping trusted proxies
        # Disabling temporarily; the filter list is so long and we should tone it down
        # xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        # if xff:
        #     hops = [h.strip() for h in xff.split(",") if h.strip()]
        #     # Work from right-most (closest to us) to left-most (original client)
        #     for raw in reversed(hops):
        #         hop_ip = _parse_ip(raw)
        #         if hop_ip and not _is_trusted(hop_ip):
        #             return str(hop_ip)

        # 4) Nothing found
        logger.debug("Client IP is in Google Cloud range, Cloudflare range, or no valid IP found. Returning '0'.")
        return '0'

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS Track Agent Email Opened")
    def track_agent_email_opened(payload: dict):
        """
        Track an email opened by a persistent agent.

        The incoming structure from Postmark is in JSON format, and but this function
        receives a Python dictionary that matches the expected structure.

        {
          "FirstOpen": true,
        }

        Args:
            payload (dict): The Postmark event payload to unpack.

        Returns:
            dict: A standardized dictionary with relevant fields extracted from the Postmark payload.
        """
        if not payload.get('Recipient'):
            logger.info("No recipient found in Postmark payload for email open event. Cannot track email open event.")
            return

        user_id = Analytics.get_user_id_from_email(payload.get('Recipient'))

        if not user_id:
            logger.info(f"No user found for email {payload.get('Recipient')}. Cannot track email open event.")
            return

        properties = {
            **Analytics.unpack_postmark_event(payload),
            'first_open': payload.get('FirstOpen', True),
        }

        Analytics.track_event(
            user_id=user_id,
            event=AnalyticsEvent.EMAIL_OPENED,
            source=AnalyticsSource.EMAIL,
            properties=properties,
            ip=payload.get('Geo', {}).get('IP', '0')
        )

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS track_agent_email_link_clicked")
    def track_agent_email_link_clicked(payload: dict):
        """
        Track a link clicked in an email by a persistent agent.

        The incoming structure from Postmark is in JSON format, but this function
        receives a Python dictionary that matches the expected structure.

        Note this is the additional structure for a link click, past the common fields

        {
          "ClickLocation": "HTML",
          "Platform": "Desktop",
          "OriginalLink": "https://example.com",
          "Metadata" : {
            "a_key" : "a_value",
            "b_key": "b_value"
           },
        }

        Args:
            payload (dict): The Postmark event payload to unpack.

        Returns:
            dict: A standardized dictionary with relevant fields extracted from the Postmark payload.
        """

        if not payload.get('Recipient'):
            logger.info("No recipient found in Postmark payload for email open event. Cannot track event email link click event.")
            return

        user_id = Analytics.get_user_id_from_email(payload.get('Recipient'))

        if not user_id:
            logger.info(f"No user found for email {payload.get('Recipient')}. Cannot track email link click event.")
            return

        properties = {
            **Analytics.unpack_postmark_event(payload),
            'click_location': payload.get('ClickLocation', 'HTML'),
            'platform': payload.get('Platform', 'Desktop'),
            'original_link': payload.get('OriginalLink', ''),
        }

        Analytics.track_event(
            user_id=Analytics.get_user_id_from_email(payload.get('Recipient')),
            event=AnalyticsEvent.EMAIL_LINK_CLICKED,
            source=AnalyticsSource.EMAIL,
            properties=properties,
            ip=payload.get('Geo', {}).get('IP', '0')
        )

    @staticmethod
    def publish_threshold_event(user_id, threshold: int, pct: int, period_ym: str, used: int = 0, entitled: int = 0):
        """
        Publish a task usage threshold event to Segment. This is used to track when a user reaches a certain
        threshold of task usage.

        Args:
            user_id (str): The ID of the user who reached the threshold.
            threshold (int): The task usage threshold that was reached.
            pct (int): The percentage of the threshold that was reached.
            period_ym (str): The period in 'YYYYMM' format for which the threshold was reached.
            used (int): The number of tasks used in the period. Defaults to 0.
            entitled (int): The number of tasks the user is entitled to in the period. Defaults to 0.
        """
        if Analytics._is_analytics_enabled():
            properties = {
                'threshold': threshold,
                'pct': pct,
                'period_ym': period_ym,
                'used': used,
                'entitled': entitled
            }
            Analytics.track_event(
                user_id=user_id,
                event=AnalyticsEvent.TASK_THRESHOLD_REACHED,
                source=AnalyticsSource.NA,
                properties=properties
            )

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS get_user_id_from_email")
    def get_user_id_from_email(email: str) -> str | None :
        """
        Extracts the user ID from an email address. Note: in future this will use PersistentAgentCommsEndpoint, since
        people could be using other email addresses for persistent agents. For now, we assume the email is a user email.

        Args:
            email (str): The email address to extract the user ID from.
        """
        try:
            user = User.objects.get(
                email=email
            )

            return str(user.id)

        except User.DoesNotExist:
            # If no user is found, we can return None or handle it as needed
            logger.warning(f"No user found for email {email}. Cannot determine user ID.")
            return None

        except User.MultipleObjectsReturned:
            # If multiple users have the same email, we can return None or handle it as needed
            logger.warning(f"Multiple users found for email {email}. Cannot determine user ID.")
            return None

    @staticmethod
    def unpack_postmark_event(payload: dict) -> dict:
        """
        Unpacks a Postmark event payload into a standardized dictionary format.
        This is useful for tracking events in Segment or Mixpanel.

        Common fields whether link clicked or email opened:

         {
          "RecordType": "Open",
          "MessageStream": "outbound",
          "Metadata": {
            "example": "value",
            "example_2": "value"
          },
          "FirstOpen": true,
          "Recipient": "john@example.com",
          "MessageID": "00000000-0000-0000-0000-000000000000",
          "ReceivedAt": "2025-05-04T03:07:19Z",
          "Platform": "WebMail",
          "ReadSeconds": 5,
          "Tag": "welcome-email",
          "UserAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.153 Safari/537.36",
          "OS": {
            "Name": "OS X 10.7 Lion",
            "Family": "OS X 10",
            "Company": "Apple Computer, Inc."
          },
          "Client": {
            "Name": "Chrome 35.0.1916.153",
            "Family": "Chrome",
            "Company": "Google"
          },
          "Geo": {
            "IP": "188.2.95.4",
            "City": "Novi Sad",
            "Country": "Serbia",
            "CountryISOCode": "RS",
            "Region": "Autonomna Pokrajina Vojvodina",
            "RegionISOCode": "VO",
            "Zip": "21000",
            "Coords": "45.2517,19.8369"
          }
        }

        Args:
            payload (dict): The Postmark event payload to unpack.

        Returns:
            dict: A standardized dictionary with relevant fields extracted from the Postmark payload.
        """
        return {
            'record_type': payload.get('RecordType'),
             # Match Segment and Mixpanel naming conventions
            'recipient': payload.get('Recipient'),
            'message_id': payload.get('MessageID'),
            'received_at': payload.get('ReceivedAt'),
            'platform': payload.get('Platform'),
            'read_seconds': payload.get('ReadSeconds'),
            'user_agent': payload.get('UserAgent'),
            'os_name': payload.get('OS', {}).get('Name'),
            'os_family': payload.get('OS', {}).get('Family'),
            'os_company': payload.get('OS', {}).get('Company'),
            'client_name': payload.get('Client', {}).get('Name'),
            'client_family': payload.get('Client', {}).get('Family'),
            'client_company': payload.get('Client', {}).get('Company'),
            # IP is a part of the track_event call
            '$city': payload.get('Geo', {}).get('City', ''),
            '$region': payload.get('Geo', {}).get('Region', ''),
            # Note: conflicting documentation on the properties in BI tools, so including both
            '$country': payload.get('Geo', {}).get('Country', ''),
            'country': payload.get('Geo', {}).get('Country', ''),
            'mp_country_code': payload.get('Geo', {}).get('CountryISOCode', ''),
            'zip': payload.get('Geo', {}).get('Zip', ''),
            'coords': payload.get('Geo', {}).get('Coords', ''),
            'metadata': payload.get('Metadata', {})
        }

PAGE_META = {
    "/pricing/":                        ("Marketing",  "Pricing"),
    "/accounts/login/":                 ("Auth",       "Login"),
    "/accounts/logout/":                ("Auth",       "Logout"),
    "/accounts/signup/":                ("Auth",       "Sign Up"),
    r"^/console/tasks/.*/$":            ("App",        "Task Details"),
    r"^/console/agents/.*/$":           ("App",        "Agent Details"),
    "/console/agents/":                 ("App",        "Agents"),
    "/console/tasks/":                  ("App",        "Tasks"),
    "/console/api-keys/":               ("App",        "API Keys"),
    "/console/billing/":                ("App",        "Billing"),
    "/console/profile/":                ("App",        "Profile"),
    "/console/":                        ("App",        "Dashboard"),
    "/support/":                        ("Support",    "Support"),
    "/docs/guides/api/":                ("Docs",       "API"),
    "/docs/guides/secrets/":            ("Docs",       "Secrets"),
    "/docs/guides/synchronous-tasks/":  ("Docs",       "Synchronous Tasks"),
    "/spawn-agent/":                    ("App",        "Spawn Agent"),
    "/":                                ("Marketing",  "Home"),
    "/blog/":                           ("Marketing",  "Blog"),
    r"^/blog/.*/$":                      ("Marketing",  "Blog Post"),
}



# We want a way to check if an IP address is in the Google Cloud range and prevent that from being recorded. For some
# reason, we are still accidentally the server IPs in Segment, so we need to filter them out to prevent skewing our
# analytics. No IP would be preferred, over a Google IP, since we don't want to record the server IPs.
def is_in_trusted_proxies(ip_str: str) -> bool:
    """
    Check if the given IP address is in the list of trusted proxies.

    Args:
        ip_str (str): The IP address to check.

    Returns:
        bool: True if the IP address is in the trusted proxies, False otherwise.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in proxy for proxy in TRUSTED_PROXIES)

    except ValueError:
        # Not a valid IPv4/IPv6 literal
        return False
