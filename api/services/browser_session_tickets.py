import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from api.models import ApiKey, BrowserSessionTicket, BrowserUseAgentTask


DEFAULT_NEXT_PATH = "/app/"
MIN_TICKET_TTL_SECONDS = 30
MAX_TICKET_TTL_SECONDS = 300
MIN_SESSION_TTL_SECONDS = 300
MAX_SESSION_TTL_SECONDS = 14_400
MAX_TICKETS_PER_USER_PER_MINUTE = 20


class BrowserSessionTicketError(ValueError):
    pass


class BrowserSessionTicketUnavailable(BrowserSessionTicketError):
    pass


class BrowserSessionTicketForbidden(BrowserSessionTicketError):
    pass


class BrowserSessionTicketInvalid(BrowserSessionTicketError):
    pass


class BrowserSessionTicketRateLimited(BrowserSessionTicketError):
    pass


@dataclass(frozen=True)
class IssuedBrowserSessionTicket:
    ticket: BrowserSessionTicket
    raw_token: str

    @property
    def login_url(self) -> str:
        path = reverse(
            "api:browser-session-ticket-consume",
            kwargs={"ticket_id": self.ticket.id},
        )
        landing_url = urljoin(
            f"{settings.PUBLIC_SITE_URL.rstrip('/')}/",
            path.lstrip("/"),
        )
        return f"{landing_url}#token={self.raw_token}"


def browser_session_tickets_available() -> bool:
    environment = settings.GOBII_RELEASE_ENV
    if environment == "prod":
        return False
    if environment == "staging" or environment.startswith("preview-pr-"):
        return True
    return environment == "local" and settings.DEBUG


def canonical_browser_session_host() -> str:
    parsed = urlsplit(settings.PUBLIC_SITE_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BrowserSessionTicketUnavailable(
            "PUBLIC_SITE_URL must be an absolute HTTP(S) URL."
        )
    return parsed.netloc.lower()


def normalize_browser_session_next_path(raw_path) -> str:
    path = DEFAULT_NEXT_PATH if raw_path in {None, ""} else str(raw_path)
    if len(path) > 512:
        raise BrowserSessionTicketInvalid("next_path must be at most 512 characters.")
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise BrowserSessionTicketInvalid("next_path must be a safe local path.")
    if any(ord(character) < 32 for character in path):
        raise BrowserSessionTicketInvalid("next_path must be a safe local path.")

    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise BrowserSessionTicketInvalid("next_path must be a safe local path.")
    return path


def issue_browser_session_ticket(
    *,
    user,
    expected_environment: str,
    request_host: str,
    next_path: str = DEFAULT_NEXT_PATH,
    purpose: str = "",
    api_key: ApiKey | None = None,
    browser_task: BrowserUseAgentTask | None = None,
) -> IssuedBrowserSessionTicket:
    if not browser_session_tickets_available():
        raise BrowserSessionTicketUnavailable(
            "Browser session tickets are available only in local development, preview, and staging."
        )

    environment = settings.GOBII_RELEASE_ENV
    if str(expected_environment or "").strip() != environment:
        raise BrowserSessionTicketInvalid(
            f"expected_environment must exactly match {environment}."
        )
    if not user or not user.is_active or not user.is_staff:
        raise BrowserSessionTicketForbidden(
            "Browser session tickets require an active staff user."
        )

    source = BrowserSessionTicket.Source.API
    if browser_task is not None:
        if browser_task.user_id != user.id:
            raise BrowserSessionTicketForbidden(
                "The browser task and web-session user must match."
            )
        source = BrowserSessionTicket.Source.GOBII_BROWSER_TASK
        if api_key is not None:
            raise BrowserSessionTicketInvalid(
                "Gobii browser-task tickets cannot also be attributed to an API key."
            )
    else:
        if (
            api_key is None
            or api_key.organization_id is not None
            or api_key.user_id != user.id
            or not api_key.is_active
        ):
            raise BrowserSessionTicketForbidden(
                "A personal API key for the authenticated staff user is required."
            )

    canonical_host = canonical_browser_session_host()
    if str(request_host or "").strip().lower() != canonical_host:
        raise BrowserSessionTicketInvalid(
            "The request host does not match this deployment's PUBLIC_SITE_URL."
        )

    normalized_purpose = str(purpose or "").strip()
    if len(normalized_purpose) > 200:
        raise BrowserSessionTicketInvalid("purpose must be at most 200 characters.")

    ttl_seconds = settings.BROWSER_SESSION_TICKET_TTL_SECONDS
    if not MIN_TICKET_TTL_SECONDS <= ttl_seconds <= MAX_TICKET_TTL_SECONDS:
        raise BrowserSessionTicketUnavailable(
            f"BROWSER_SESSION_TICKET_TTL_SECONDS must be between {MIN_TICKET_TTL_SECONDS} and {MAX_TICKET_TTL_SECONDS}."
        )

    if BrowserSessionTicket.objects.filter(
        user=user,
        created_at__gte=timezone.now() - timedelta(minutes=1),
    ).count() >= MAX_TICKETS_PER_USER_PER_MINUTE:
        raise BrowserSessionTicketRateLimited(
            "Too many browser session tickets were created recently."
        )

    raw_token = secrets.token_urlsafe(32)
    ticket = BrowserSessionTicket.objects.create(
        user=user,
        api_key=api_key,
        browser_task=browser_task,
        source=source,
        purpose=normalized_purpose,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        environment=environment,
        host=canonical_host,
        next_path=normalize_browser_session_next_path(next_path),
        expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
    )
    return IssuedBrowserSessionTicket(ticket=ticket, raw_token=raw_token)


def issue_gobii_browser_task_session(browser_task_id: str) -> IssuedBrowserSessionTicket:
    browser_task = BrowserUseAgentTask.objects.select_related("user").get(
        id=browser_task_id
    )
    return issue_browser_session_ticket(
        user=browser_task.user,
        expected_environment=settings.GOBII_RELEASE_ENV,
        request_host=canonical_browser_session_host(),
        next_path=DEFAULT_NEXT_PATH,
        purpose="Gobii UI QA browser task",
        browser_task=browser_task,
    )


def consume_browser_session_ticket(
    *,
    ticket_id,
    raw_token: str,
    request_host: str,
) -> BrowserSessionTicket:
    token_hash = hashlib.sha256(str(raw_token or "").encode("utf-8")).hexdigest()
    now = timezone.now()

    with transaction.atomic():
        # Keep the locking query join-free: PostgreSQL cannot FOR UPDATE the
        # nullable side of the optional API-key relationship.
        ticket = (
            BrowserSessionTicket.objects.select_for_update()
            .filter(id=ticket_id)
            .first()
        )
        if ticket is None or not secrets.compare_digest(
            ticket.token_hash,
            token_hash,
        ):
            raise BrowserSessionTicketInvalid("Browser session ticket is invalid.")
        if ticket.consumed_at is not None:
            raise BrowserSessionTicketInvalid(
                "Browser session ticket has already been consumed."
            )
        if ticket.expires_at <= now:
            raise BrowserSessionTicketInvalid("Browser session ticket has expired.")
        if not browser_session_tickets_available():
            raise BrowserSessionTicketInvalid(
                "Browser session tickets are unavailable in this environment."
            )
        if ticket.environment != settings.GOBII_RELEASE_ENV:
            raise BrowserSessionTicketInvalid(
                "Browser session ticket belongs to another environment."
            )
        if ticket.host != str(request_host or "").strip().lower():
            raise BrowserSessionTicketInvalid(
                "Browser session ticket belongs to another host."
            )
        if not ticket.user.is_active or not ticket.user.is_staff:
            raise BrowserSessionTicketInvalid(
                "Browser session ticket user is no longer authorized."
            )
        if ticket.source == BrowserSessionTicket.Source.API:
            if (
                ticket.api_key is None
                or not ticket.api_key.is_active
                or ticket.api_key.organization_id is not None
                or ticket.api_key.user_id != ticket.user_id
            ):
                raise BrowserSessionTicketInvalid(
                    "Browser session ticket API key is no longer authorized."
                )

        ticket.consumed_at = now
        ticket.save(update_fields=["consumed_at"])
        return ticket


def browser_session_ttl_seconds() -> int:
    ttl_seconds = settings.BROWSER_SESSION_TICKET_SESSION_TTL_SECONDS
    if not MIN_SESSION_TTL_SECONDS <= ttl_seconds <= MAX_SESSION_TTL_SECONDS:
        raise BrowserSessionTicketUnavailable(
            f"BROWSER_SESSION_TICKET_SESSION_TTL_SECONDS must be between {MIN_SESSION_TTL_SECONDS} and {MAX_SESSION_TTL_SECONDS}."
        )
    return ttl_seconds
