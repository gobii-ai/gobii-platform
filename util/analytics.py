import ipaddress
from datetime import datetime
from typing import Any

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

GOOGLE_CIDR_RANGE = ipaddress.ip_network("35.184.0.0/13")
TRUSTED_PROXIES = [GOOGLE_CIDR_RANGE]


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

    # Persistent Agent Events
    PERSISTENT_AGENT_CREATED = 'Persistent Agent Created'
    PERSISTENT_AGENT_UPDATED = 'Persistent Agent Updated'
    PERSISTENT_AGENT_DELETED = 'Persistent Agent Deleted'
    PERSISTENT_AGENT_VIEWED = 'Persistent Agent Viewed'
    PERSISTENT_AGENT_SOFT_EXPIRED = 'Persistent Agent Soft Expired'
    PERSISTENT_AGENT_SHUTDOWN = 'Persistent Agent Shutdown'
    PERSISTENT_AGENT_CHARTER_SUBMIT = 'Persistent Agent Charter Submitted'
    PERSISTENT_AGENTS_LISTED = 'Persistent Agents Listed'
    PERSISTENT_AGENT_EMAIL_SENT = 'Persistent Agent Message Sent'
    PERSISTENT_AGENT_EMAIL_RECEIVED = 'Persistent Agent Message Received'
    PERSISTENT_AGENT_EMAIL_OUT_OF_CREDITS = 'Persistent Agent Out of Credits Email'

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

    # API Key Events
    API_KEY_CREATED = 'API Key Created'
    API_KEY_DELETED = 'API Key Deleted'
    API_KEY_REVOKED = 'API Key Revoked'

    # Console Events
    CONSOLE_HOME_VIEWED = 'Console Home Viewed'

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
    def track(user_id, event, properties, context = {}, ip: str = None, message_id: str = None, timestamp = None):
        if Analytics._is_analytics_enabled():
            with traced("ANALYTICS Track"):
                context['ip'] = '0'
                analytics.track(user_id, event, properties, context, timestamp, None, None, message_id)

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS Track Event")
    def track_event(user_id, event: AnalyticsEvent, source: AnalyticsSource, properties: dict = {}, ip: str = None):
        if Analytics._is_analytics_enabled():
            with traced("ANALYTICS Track Event"):
                properties['medium'] = str(source)
                context = {
                    'ip': '0',
                }

                analytics.track(user_id, event, properties, context)

    @staticmethod
    @tracer.start_as_current_span("ANALYTICS Track Event Anonymous")
    def track_event_anonymous(anonymous_id: str, event: AnalyticsEvent, source: AnalyticsSource, properties: dict = {}, ip: str = None):
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
        Get the client IP address from the request, considering trusted proxies and Google Cloud IP ranges.
        This function checks the `HTTP_X_FORWARDED_FOR` header for the original client IP address, and falls back to
        `REMOTE_ADDR` if not present.

        It also checks if the IP address is in the Google Cloud range and returns '0' if it is, to prevent skewing our
        analytics data.

        Args:
            request (HttpRequest): The Django request object containing metadata about the request.
        Returns:
            str: The client IP address, or '0' if the IP is in the Google Cloud range or if no valid IP is found.
        """
        xff = request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR")

        # split & strip spaces
        candidates = [ip.strip() for ip in xff.split(",")] if xff else []

        remote_addr = request.META.get("REMOTE_ADDR")

        if remote_addr is not None:
            candidates.append(remote_addr.strip())

        # Walk **right-to-left**, discarding any address that belongs to a proxy we trust
        for ip in reversed(candidates):
            if not is_in_trusted_proxies(ip):
                # If the IP is not in the Google Cloud range, we return it
                logger.debug(f"Client IP: {ip}")
                return ip


        # Fallback: we only saw proxies
        logger.debug("Client IP is in Google Cloud range or no valid IP found. Returning '0'.")
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

            return user.id

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
