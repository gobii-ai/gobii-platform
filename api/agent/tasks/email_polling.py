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
import re
from datetime import timedelta
from typing import Iterable, List, Tuple, Optional
import ssl
from email import policy
from email.parser import BytesParser
from email.header import decode_header, make_header
from email.utils import parseaddr

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from opentelemetry import trace

from api.models import AgentEmailAccount, CommsChannel, PersistentAgentCommsEndpoint
from api.agent.comms.imap_adapter import ImapEmailAdapter, ImapParsedContext
from api.agent.comms.email_oauth import build_xoauth2_string, resolve_oauth_identity_and_token
from api.agent.comms.message_service import ingest_inbound_message
from config.redis_client import get_redis_client
from pottery import Redlock

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)


MIN_POLL_INTERVAL_SEC = 30
MAX_ENQUEUES_PER_RUN = 200
BATCH_SIZE = 100
MAX_MESSAGES_PER_ACCOUNT = 500
IMAP_TIMEOUT_SEC = 60
UID_SEARCH_CHUNK_SIZE = 1000

_UIDNEXT_RE = re.compile(r"\d+")


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


def _parse_uidnext_value(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="ignore")
    else:
        text = str(value)
    match = _UIDNEXT_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _fetch_message_bytes(client: imaplib.IMAP4, uid: str) -> Optional[bytes]:
    # Fetch message body using BODY.PEEK[] to avoid setting \Seen
    typ, data = client.uid("FETCH", uid, "(BODY.PEEK[])")
    if typ != "OK" or not data:
        return None
    # Response may be a list of tuples and bytes; return the largest bytes payload
    best: Optional[bytes] = None
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            payload = bytes(item[1])
            if best is None or len(payload) > len(best):
                best = payload
        elif isinstance(item, (bytes, bytearray)):
            # often trailing b')' or similar, ignore
            continue
    return best


def _extract_sender_from_header_bytes(hdr_bytes: bytes) -> Optional[str]:
    try:
        # Parse as headers-only; BytesParser can handle partial messages
        msg = BytesParser(policy=policy.default).parsebytes(hdr_bytes)
        raw_from = msg.get("From")
        if not raw_from:
            return None
        try:
            decoded = str(make_header(decode_header(raw_from)))
        except Exception:
            decoded = raw_from
        return (parseaddr(decoded)[1] or decoded).strip()
    except Exception:
        return None


def _fetch_sender_address(client: imaplib.IMAP4, uid: str) -> Optional[str]:
    """Fetch just the sender address via header-only fetch to avoid full body download.

    Falls back to attempting to parse From from a BODY[] response if server
    returns that structure (e.g., in mocks).
    """
    try:
        typ, data = client.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
        if typ == "OK" and data:
            for item in data:
                if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                    addr = _extract_sender_from_header_bytes(bytes(item[1]))
                    if addr:
                        return addr
        # Fallback: some mocks return BODY[] even for header fetch; try to parse
        if data:
            for item in data:
                if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                    addr = _extract_sender_from_header_bytes(bytes(item[1]))
                    if addr:
                        return addr
    except Exception:
        # Ignore and fallback to full fetch path
        pass
    return None


def _update_success(acct: AgentEmailAccount, now, last_uid: str, uidvalidity: Optional[str]) -> None:
    acct.last_seen_uid = f"v:{uidvalidity}:{last_uid}" if uidvalidity else str(last_uid)
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
        client = imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT_SEC)
    else:
        client = imaplib.IMAP4(host, port, timeout=IMAP_TIMEOUT_SEC)
        if acct.imap_security == AgentEmailAccount.ImapSecurity.STARTTLS:
            ctx = ssl.create_default_context()
            client.starttls(ssl_context=ctx)
    # Login
    if acct.imap_auth == AgentEmailAccount.ImapAuthMode.OAUTH2:
        identity, access_token, _credential = resolve_oauth_identity_and_token(acct, "imap")
        auth_string = build_xoauth2_string(identity, access_token)
        client.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
    elif acct.imap_auth != AgentEmailAccount.ImapAuthMode.NONE:
        client.login(acct.imap_username or "", acct.get_imap_password() or "")
    # Select folder
    folder = acct.imap_folder or "INBOX"
    # Select folder in read-write mode to allow marking messages as read
    typ, _ = client.select(folder, readonly=False)
    if typ != "OK":
        raise RuntimeError(f"Failed to select folder {folder}")
    return client


def _ingest_uid(client: imaplib.IMAP4, acct: AgentEmailAccount, uid: str) -> bool:
    """Fetch and ingest a single UID. Returns True if considered processed.

    Non-whitelisted senders are treated as processed so we don't reprocess them
    on the next poll.
    """
    try:
        endpoint = acct.endpoint  # type: ignore[assignment]
        agent = getattr(endpoint, "owner_agent", None)

        # Optimize: check whitelist via header-only fetch to avoid downloading body
        if agent is not None:
            sender = _fetch_sender_address(client, uid)
            if sender and not agent.is_sender_whitelisted(CommsChannel.EMAIL, sender):
                logger.info(
                    "IMAP message from %s is not whitelisted for agent %s; skipping",
                    sender, getattr(agent, "id", None),
                )
                return True

        raw = _fetch_message_bytes(client, uid)
        if not raw:
            return True  # nothing to do, treat as processed

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
        # Mark message as read (\Seen) after successful ingestion
        try:
            client.uid("STORE", uid, "+FLAGS", r"(\Seen)")
        except Exception:
            # Non-fatal; continue
            pass
        return True
    except Exception as e:
        logger.error("Error ingesting UID %s for %s: %s", uid, acct.endpoint.address, e, exc_info=True)
        return False


def _uid_search_new(client: imaplib.IMAP4, last_seen: str | None) -> List[str]:
    # Compute search range from last_seen which may be composite "v:<validity>:<uid>"
    start = 0
    if last_seen:
        try:
            if last_seen.startswith("v:"):
                parts = last_seen.split(":", 2)
                if len(parts) == 3:
                    start = int(parts[2])
                else:
                    start = 0
            else:
                start = int(last_seen)
        except Exception:
            start = 0

    # UID SEARCH for newer UIDs
    # Use the UID search criteria explicitly to ensure numeric range is interpreted as UIDs,
    # not message sequence numbers. Parentheses are accepted and common across servers.
    # Only unread messages are considered. Combine UNSEEN with UID range, with a fallback
    query = f"(UNSEEN UID {start + 1}:*)" if start > 0 else "(UNSEEN UID 1:*)"
    typ, data = client.uid("SEARCH", None, query)
    if typ != "OK":
        return _uid_search_unseen_fallback(client, start)
    uids = _parse_uid_list(data)
    # Best-effort debug to help diagnose repeated processing in production
    try:
        logger.debug("IMAP UID SEARCH %s returned %d UIDs (start=%d)", query, len(uids), start)
    except Exception:
        pass
    # Ascending order for processing
    try:
        uids = sorted(uids, key=lambda s: int(s))
    except Exception:
        pass
    return uids


def _get_uidnext(client: imaplib.IMAP4) -> Optional[int]:
    try:
        typ, data = client.response("UIDNEXT")
        if typ == "OK" and data and isinstance(data, list) and data[0]:
            return _parse_uidnext_value(data[0])
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as exc:
        logger.warning("Failed to get UIDNEXT: %s", exc)
    return None


def _uid_search_unseen_fallback(client: imaplib.IMAP4, start: int) -> List[str]:
    # Fallback: search UNSEEN and filter client-side by UID range
    typ2, data2 = client.uid("SEARCH", None, "UNSEEN")
    if typ2 != "OK" or not data2:
        return []
    uids = _parse_uid_list(data2)
    try:
        min_uid = start + 1 if start > 0 else 1
        uids = [u for u in uids if int(u) >= min_uid]
        uids = sorted(uids, key=lambda s: int(s))
    except ValueError as exc:
        logger.warning(
            "Error processing UIDs in fallback search: %s. Results may be incomplete or unsorted.",
            exc,
        )
    return uids


def _uid_search_new_chunked(client: imaplib.IMAP4, start: int) -> List[str]:
    uidnext = _get_uidnext(client)
    if not uidnext:
        return _uid_search_unseen_fallback(client, start)
    end = max(uidnext - 1, start)
    if end <= start:
        return []
    uids: List[str] = []
    cursor = start + 1
    while cursor <= end:
        chunk_end = min(cursor + UID_SEARCH_CHUNK_SIZE - 1, end)
        query = f"(UNSEEN UID {cursor}:{chunk_end})"
        typ, data = client.uid("SEARCH", None, query)
        if typ != "OK":
            logger.warning(
                "IMAP UID SEARCH chunk returned %s for %s; falling back to UNSEEN search",
                typ,
                query,
            )
            return _uid_search_unseen_fallback(client, start)
        uids.extend(_parse_uid_list(data))
        cursor = chunk_end + 1
    try:
        uids = sorted(set(uids), key=lambda s: int(s))
    except ValueError as exc:
        logger.warning("Error sorting UIDs in chunked search: %s", exc)
    return uids


def _highest_uid(client: imaplib.IMAP4) -> Optional[str]:
    """Return the highest UID in the selected folder, or None if empty."""
    try:
        typ, data = client.uid("SEARCH", None, "(UID 1:*)")
        if typ != "OK" or not data:
            return None
        uids = _parse_uid_list(data)
        if not uids:
            return None
        try:
            return str(max(int(u) for u in uids))
        except Exception:
            return uids[-1]
    except Exception:
        return None


def _get_uidvalidity(client: imaplib.IMAP4) -> Optional[str]:
    try:
        typ, data = client.response("UIDVALIDITY")
        if typ == "OK" and data and isinstance(data, list) and data[0]:
            val = data[0]
            if isinstance(val, bytes):
                return val.decode("utf-8", errors="ignore").strip()
            return str(val).strip()
    except Exception:
        pass
    return None


def _parse_last_seen(last_seen: Optional[str]) -> Tuple[Optional[str], int]:
    if not last_seen:
        return None, 0
    try:
        if last_seen.startswith("v:"):
            _, v, uid = last_seen.split(":", 2)
            return v, int(uid)
        return None, int(last_seen)
    except Exception:
        return None, 0


def _agent_env_matches(acct: AgentEmailAccount) -> bool:
    """True when the account's owner agent matches the current release environment."""
    try:
        owner_agent = acct.endpoint.owner_agent
    except Exception:
        return False
    return bool(owner_agent and owner_agent.execution_environment == settings.GOBII_RELEASE_ENV)


def _poll_account_locked(acct: AgentEmailAccount) -> None:
    now = timezone.now()
    client: imaplib.IMAP4 | None = None
    try:
        with tracer.start_as_current_span("email.imap.poll") as span:
            span.set_attribute("imap.host", acct.imap_host)
            span.set_attribute("imap.port", int(acct.imap_port or 0))
            span.set_attribute("imap.security", acct.imap_security)
            span.set_attribute("imap.folder", acct.imap_folder or "INBOX")

            client = _connect_imap(acct)

            # Handle UIDVALIDITY; reset if changed
            current_validity = _get_uidvalidity(client)
            stored_validity, stored_uid = _parse_last_seen(acct.last_seen_uid)
            if stored_validity and current_validity and stored_validity != current_validity:
                logger.info("UIDVALIDITY changed for %s: %s -> %s; resetting last_seen_uid", acct.endpoint.address, stored_validity, current_validity)
                stored_uid = 0

            # Initialize baseline on first run: skip historical mail entirely.
            if not acct.last_seen_uid:
                latest = _highest_uid(client)
                if latest is not None:
                    _update_success(acct, timezone.now(), str(latest), current_validity)
                    return

            # Search for newer, unread UIDs from stored_uid
            base_marker = f"v:{current_validity}:{stored_uid}" if current_validity is not None else str(stored_uid)
            try:
                uids = _uid_search_new(client, base_marker)
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as exc:
                logger.warning(
                    "IMAP UID SEARCH failed for %s: %s; retrying with chunked search",
                    acct.endpoint.address,
                    exc,
                )
                if client is not None:
                    try:
                        client.logout()
                    except Exception:
                        try:
                            client.shutdown()  # type: ignore[attr-defined]
                        except Exception:
                            pass
                client = _connect_imap(acct)
                uids = _uid_search_new_chunked(client, stored_uid)
            # Align with plan: new_uid_count; keep prior metric name minimal
            try:
                span.set_attribute("new_uid_count", len(uids))
            except Exception:
                pass
            if not uids:
                acct.last_polled_at = now
                acct.connection_last_ok_at = now
                acct.connection_error = ""
                acct.save(update_fields=["last_polled_at", "connection_last_ok_at", "connection_error"])
                return

            # Process in batches
            # Enforce per-account cap to avoid long catch-ups in single run
            capped_uids = uids[:MAX_MESSAGES_PER_ACCOUNT]
            processed_highest: Optional[str] = None
            for i in range(0, len(capped_uids), BATCH_SIZE):
                batch = capped_uids[i : i + BATCH_SIZE]
                for uid in batch:
                    ok = _ingest_uid(client, acct, uid)
                    if ok:
                        processed_highest = uid

            if processed_highest is not None:
                _update_success(acct, timezone.now(), str(processed_highest), current_validity)
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


@shared_task(bind=True, name="api.agent.tasks.poll_imap_inbox", expires=600, ignore_result=True)
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
        owner_agent = acct.endpoint.owner_agent
        if not _agent_env_matches(acct):
            logger.info(
                "IMAP poll skipped for account %s due to env mismatch (agent_env=%s, expected=%s)",
                account_id,
                getattr(owner_agent, "execution_environment", None),
                settings.GOBII_RELEASE_ENV,
            )
            return
        _poll_account_locked(acct)
    except AgentEmailAccount.DoesNotExist:
        logger.warning("AgentEmailAccount %s does not exist; skipping", account_id)
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:
                pass


@shared_task(bind=True, name="api.agent.tasks.poll_imap_inboxes", expires=90, ignore_result=True)
def poll_imap_inboxes(self) -> None:
    """Dispatcher: find due inbound-enabled accounts and enqueue per-account tasks."""
    now = timezone.now()

    # Select a pool of candidates; filter minimally in DB, due filtering in Python
    candidates = (
        AgentEmailAccount.objects.select_related("endpoint")
        # Only poll accounts whose owner agent matches the current release env.
        .filter(
            is_inbound_enabled=True,
            endpoint__owner_agent__execution_environment=settings.GOBII_RELEASE_ENV,
        )
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
            # Enqueue; task has an expires set in its decorator and will age off if unprocessed
            poll_imap_inbox.delay(account_id)
        except Exception:
            logger.warning("Failed to enqueue poll task for %s", account_id, exc_info=True)
