from __future__ import annotations

"""Celery tasks for polling inbound IMAP mailboxes.

Design:
- Dispatcher task runs frequently to enqueue per-account polls for due accounts.
- Per-account task acquires a distributed lock, connects to IMAP, fetches new
  messages via UID, parses them via ImapEmailAdapter, enforces whitelist, and
  ingests messages into the existing pipeline.
"""

import imaplib
import logging
import random
from datetime import timedelta
from typing import Iterable, List, Tuple

from celery import shared_task
from django.utils import timezone
from opentelemetry import trace

from api.models import AgentEmailAccount, CommsChannel, PersistentAgentCommsEndpoint
from api.agent.comms.imap_adapter import ImapEmailAdapter, ImapParsedContext
from api.agent.comms.message_service import ingest_inbound_message
from config.redis_client import get_redis_client
from pottery import Redlock

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)


MIN_POLL_INTERVAL_SEC = 30
MAX_ENQUEUES_PER_RUN = 200
BATCH_SIZE = 100
IMAP_TIMEOUT_SEC = 60


def _is_due(acct: AgentEmailAccount, now) -> bool:
    if not acct.is_inbound_enabled:
        return False
    if acct.backoff_until and acct.backoff_until > now:
        return False
    interval = max(int(acct.poll_interval_sec or 0), MIN_POLL_INTERVAL_SEC)
    # Add small jitter (±10%)
    jitter_factor = 1.0 + random.uniform(-0.1, 0.1)
    jittered = int(interval * jitter_factor)
    last = acct.last_polled_at
    if not last:
        return True
    return (now - last).total_seconds() >= jittered


def _parse_uid_list(raw: Iterable[bytes]) -> List[str]:
    uids: List[str] = []
    for blob in raw or []:
        if not blob:
            continue
        if isinstance(blob, bytes):
            text = blob.decode("utf-8", errors="ignore").strip()
            if not text:
                continue
            parts = text.split()
            uids.extend([p for p in parts if p.isdigit()])
    return uids


def _fetch_message_bytes(client: imaplib.IMAP4, uid: str) -> bytes | None:
    # Fetch message body using BODY.PEEK[] to avoid setting \Seen
    typ, data = client.uid("FETCH", uid, "(BODY.PEEK[])")
    if typ != "OK" or not data:
        return None
    # Response may be a list of tuples and bytes; return the largest bytes payload
    best: bytes | None = None
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            payload = bytes(item[1])
            if best is None or len(payload) > len(best):
                best = payload
        elif isinstance(item, (bytes, bytearray)):
            # often trailing b')' or similar, ignore
            continue
    return best


def _update_success(acct: AgentEmailAccount, now, last_uid: str) -> None:
    acct.last_seen_uid = str(last_uid)
    acct.last_polled_at = now
    acct.connection_last_ok_at = now
    acct.connection_error = ""
    acct.save(update_fields=[
        "last_seen_uid", "last_polled_at", "connection_last_ok_at", "connection_error"
    ])


def _update_error_backoff(acct: AgentEmailAccount, err: Exception) -> None:
    now = timezone.now()
    # Determine previous backoff remaining; double it up to 1 hour
    base = 120  # 2 minutes
    max_backoff = 3600
    next_delay = base
    try:
        if acct.backoff_until and acct.backoff_until > now:
            remaining = int((acct.backoff_until - now).total_seconds())
            next_delay = min(max_backoff, max(base, remaining * 2))
    except Exception:
        next_delay = base

    acct.connection_error = str(err)
    acct.backoff_until = now + timedelta(seconds=next_delay)
    acct.last_polled_at = now
    acct.save(update_fields=["connection_error", "backoff_until", "last_polled_at"])


def _connect_imap(acct: AgentEmailAccount) -> imaplib.IMAP4:
    host = acct.imap_host
    port = int(acct.imap_port or (993 if acct.imap_security == AgentEmailAccount.ImapSecurity.SSL else 143))

    if acct.imap_security == AgentEmailAccount.ImapSecurity.SSL:
        client = imaplib.IMAP4_SSL(host, port)
    else:
        client = imaplib.IMAP4(host, port)
        if acct.imap_security == AgentEmailAccount.ImapSecurity.STARTTLS:
            client.starttls()
    # Login
    client.login(acct.imap_username or "", acct.get_imap_password() or "")
    # Select folder
    folder = acct.imap_folder or "INBOX"
    typ, _ = client.select(folder, readonly=True)
    if typ != "OK":
        raise RuntimeError(f"Failed to select folder {folder}")
    return client


def _ingest_uid(client: imaplib.IMAP4, acct: AgentEmailAccount, uid: str) -> bool:
    """Fetch and ingest a single UID. Returns True if considered processed.

    Non-whitelisted senders are treated as processed so we don't reprocess them
    on the next poll.
    """
    try:
        raw = _fetch_message_bytes(client, uid)
        if not raw:
            return True  # nothing to do, treat as processed

        endpoint = acct.endpoint  # type: ignore[assignment]
        agent = getattr(endpoint, "owner_agent", None)

        parsed = ImapEmailAdapter.parse_bytes(
            raw,
            recipient_address=endpoint.address,
            ctx=ImapParsedContext(uid=str(uid), folder=acct.imap_folder or "INBOX"),
        )

        # Enforce whitelist if we have an agent
        if agent is not None and not agent.is_sender_whitelisted(CommsChannel.EMAIL, parsed.sender):
            logger.info(
                "IMAP message from %s is not whitelisted for agent %s; skipping",
                parsed.sender, getattr(agent, "id", None),
            )
            return True

        ingest_inbound_message(CommsChannel.EMAIL, parsed)
        return True
    except Exception as e:
        logger.error("Error ingesting UID %s for %s: %s", uid, acct.endpoint.address, e, exc_info=True)
        return False


def _uid_search_new(client: imaplib.IMAP4, last_seen: str | None) -> List[str]:
    # Compute search range
    start = 0
    try:
        start = int(last_seen or 0)
    except Exception:
        start = 0

    # UID SEARCH for newer UIDs
    query = f"UID {start + 1}:*" if start > 0 else "ALL"
    typ, data = client.uid("SEARCH", None, query)
    if typ != "OK":
        return []
    uids = _parse_uid_list(data)
    # Ascending order for processing
    try:
        uids = sorted(uids, key=lambda s: int(s))
    except Exception:
        pass
    return uids


def _poll_account_locked(acct: AgentEmailAccount) -> None:
    now = timezone.now()
    client: imaplib.IMAP4 | None = None
    try:
        with tracer.start_as_current_span("IMAP Poll Account") as span:
            span.set_attribute("imap.host", acct.imap_host)
            span.set_attribute("imap.port", int(acct.imap_port or 0))
            span.set_attribute("imap.security", acct.imap_security)
            span.set_attribute("imap.folder", acct.imap_folder or "INBOX")

            client = _connect_imap(acct)

            uids = _uid_search_new(client, acct.last_seen_uid or "")
            span.set_attribute("imap.uids.count", len(uids))
            if not uids:
                acct.last_polled_at = now
                acct.connection_last_ok_at = now
                acct.connection_error = ""
                acct.save(update_fields=["last_polled_at", "connection_last_ok_at", "connection_error"])
                return

            # Process in batches
            processed_highest = None
            for i in range(0, len(uids), BATCH_SIZE):
                batch = uids[i : i + BATCH_SIZE]
                for uid in batch:
                    ok = _ingest_uid(client, acct, uid)
                    # Track highest UID we have attempted regardless of result to avoid reprocessing
                    processed_highest = uid

            if processed_highest is not None:
                _update_success(acct, timezone.now(), str(processed_highest))
    except Exception as e:
        logger.error("IMAP poll error for %s: %s", getattr(acct.endpoint, "address", None), e, exc_info=True)
        _update_error_backoff(acct, e)
    finally:
        try:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    client.shutdown()  # type: ignore[attr-defined]
        except Exception:
            pass


@shared_task(bind=True, name="api.agent.tasks.poll_imap_inbox")
def poll_imap_inbox(self, account_id: str) -> None:
    """Poll a single IMAP inbox for the given account ID (endpoint PK)."""
    # Acquire distributed lock
    redis_client = get_redis_client()
    lock_key = f"imap-poll:{account_id}"
    lock = Redlock(key=lock_key, masters={redis_client}, auto_release_time=600)
    acquired = False
    try:
        if not lock.acquire(blocking=True, timeout=1):
            logger.info("IMAP poll skipped (lock busy) for account %s", account_id)
            return
        acquired = True

        acct = AgentEmailAccount.objects.select_related("endpoint__owner_agent").get(pk=account_id)
        _poll_account_locked(acct)
    except AgentEmailAccount.DoesNotExist:
        logger.warning("AgentEmailAccount %s does not exist; skipping", account_id)
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:
                pass


@shared_task(bind=True, name="api.agent.tasks.poll_imap_inboxes")
def poll_imap_inboxes(self) -> None:
    """Dispatcher: find due inbound-enabled accounts and enqueue per-account tasks."""
    now = timezone.now()

    # Select a pool of candidates; filter minimally in DB, due filtering in Python
    candidates = (
        AgentEmailAccount.objects.select_related("endpoint")
        .filter(is_inbound_enabled=True)
        .order_by("-updated_at")
    )

    due_ids: List[str] = []
    for acct in candidates:
        if _is_due(acct, now):
            due_ids.append(str(acct.pk))
            if len(due_ids) >= MAX_ENQUEUES_PER_RUN:
                break

    random.shuffle(due_ids)
    for account_id in due_ids:
        try:
            poll_imap_inbox.delay(account_id)
        except Exception:
            logger.warning("Failed to enqueue poll task for %s", account_id, exc_info=True)

