from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional
import signal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from imapclient import IMAPClient

from api.models import AgentEmailAccount
from api.agent.tasks import poll_imap_inbox
from config.redis_client import get_redis_client


logger = logging.getLogger(__name__)


def _runner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"


@dataclass
class Watcher:
    account_id: str
    thread: threading.Thread
    stop: threading.Event
    lease_key: str
    lease_value: str
    address: Optional[str] = None
    config_sig: Optional[str] = None


def _acct_config_sig(acct: AgentEmailAccount) -> str:
    """Return a lightweight signature of IMAP connection-relevant fields.

    Changing any of these should cause a watcher reconnect to pick up new settings.
    """
    try:
        host = acct.imap_host or ""
        port = int(acct.imap_port or (993 if acct.imap_security == AgentEmailAccount.ImapSecurity.SSL else 143))
        sec = acct.imap_security or ""
        user = acct.imap_username or ""
        folder = acct.imap_folder or "INBOX"
        return f"{host}|{port}|{sec}|{user}|{folder}"
    except Exception:
        return ""


class Command(BaseCommand):
    help = "Run IMAP IDLE watchers that trigger per-account poll tasks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max",
            type=int,
            default=getattr(settings, "IMAP_IDLE_MAX_CONNECTIONS", 200),
            help="Maximum concurrent IDLE watchers in this process.",
        )
        parser.add_argument(
            "--scan-interval",
            type=int,
            default=getattr(settings, "IMAP_IDLE_SCAN_INTERVAL_SEC", 30),
            help="Seconds between DB scans for eligible accounts.",
        )
        parser.add_argument(
            "--reissue",
            type=int,
            default=getattr(settings, "IMAP_IDLE_REISSUE_SEC", 1500),
            help="Seconds between IDLE re-issue on a long-lived connection.",
        )
        parser.add_argument(
            "--debounce",
            type=int,
            default=getattr(settings, "IMAP_IDLE_DEBOUNCE_SEC", 10),
            help="Debounce window for IDLE-triggered poll enqueues (seconds).",
        )
        parser.add_argument(
            "--lease-ttl",
            type=int,
            default=getattr(settings, "IMAP_IDLE_LEASE_TTL_SEC", 60),
            help="Redis lease TTL in seconds (heartbeats refresh this before expiry).",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "IMAP_IDLE_ENABLED", False):
            logger.warning("IMAP_IDLE_ENABLED is False; starting anyway. Set to True to enable by default.")

        max_watchers: int = int(options["max"])  # per process
        scan_interval: int = int(options["scan_interval"])  # seconds
        idle_reissue_sec: int = int(options["reissue"])  # seconds
        debounce_sec: int = int(options["debounce"])  # seconds
        lease_ttl: int = int(options["lease_ttl"])  # seconds

        redis = get_redis_client()
        rid = _runner_id()
        logger.info(
            "Starting IMAP IDLE runner id=%s max=%d scan=%ds reissue=%ds ttl=%ds",
            rid, max_watchers, scan_interval, idle_reissue_sec, lease_ttl,
        )

        watchers: Dict[str, Watcher] = {}
        last_logged_keys: Optional[tuple[str, ...]] = None
        stop_main = threading.Event()
        rescan_now = threading.Event()

        def _sig_handler(signum, frame):
            logger.info("Received signal %s; shutting down IMAP IDLE runner…", signum)
            stop_main.set()

        # Graceful shutdown on SIGINT/SIGTERM (K8s)
        try:
            signal.signal(signal.SIGINT, _sig_handler)
            signal.signal(signal.SIGTERM, _sig_handler)
        except Exception:
            pass

        # Queue listener thread: wake main loop early when notified
        queue_key = "imap-idle:queue"

        def _queue_listener():
            while not stop_main.is_set():
                try:
                    item = redis.blpop(queue_key, timeout=1)
                    if item:
                        _, account_id = item
                        logger.info("Received IMAP IDLE notify for account %s; triggering rescan", account_id)
                        rescan_now.set()
                except Exception:
                    # Ignore and continue
                    pass

        q_thread = threading.Thread(target=_queue_listener, name="imap-idle-queue", daemon=True)
        q_thread.start()

        try:
            while not stop_main.is_set():
                # Clear immediate rescan trigger at loop start
                rescan_now.clear()
                # 1) Reap dead watchers and release leases
                for acct_id, w in list(watchers.items()):
                    if not w.thread.is_alive():
                        try:
                            cur = redis.get(w.lease_key)
                            if cur == w.lease_value:
                                redis.delete(w.lease_key)
                        except Exception:
                            pass
                        watchers.pop(acct_id, None)

                # 2) Query eligible accounts
                eligible = (
                    AgentEmailAccount.objects.select_related("endpoint")
                    .filter(is_inbound_enabled=True, imap_idle_enabled=True)
                    .order_by("-updated_at")
                )

                # 3) Stop watchers whose account is no longer eligible
                eligible_ids = {str(a.pk) for a in eligible}
                for acct_id, w in list(watchers.items()):
                    if acct_id not in eligible_ids:
                        logger.info("Stopping watcher for %s (no longer eligible)", acct_id)
                        w.stop.set()
                        try:
                            cur = redis.get(w.lease_key)
                            if cur == w.lease_value:
                                redis.delete(w.lease_key)
                        except Exception:
                            pass
                        watchers.pop(acct_id, None)

                # 4) Restart watchers whose connection-relevant config changed
                for acct in eligible:
                    acct_id = str(acct.pk)
                    if acct_id in watchers:
                        sig = _acct_config_sig(acct)
                        w = watchers[acct_id]
                        if w.config_sig and w.config_sig != sig:
                            logger.info("Restarting watcher for %s due to config change (endpoint=%s)", acct_id, getattr(acct.endpoint, "address", None))
                            w.stop.set()
                            try:
                                cur = redis.get(w.lease_key)
                                if cur == w.lease_value:
                                    redis.delete(w.lease_key)
                            except Exception:
                                pass
                            watchers.pop(acct_id, None)

                # 5) Start new watchers up to capacity, acquiring a Redis lease
                for acct in eligible:
                    if len(watchers) >= max_watchers:
                        break
                    acct_id = str(acct.pk)
                    if acct_id in watchers:
                        continue
                    lease_key = f"imap-idle:watch:{acct_id}"
                    lease_val = rid
                    try:
                        # SETNX with TTL to acquire lease cross-process
                        ok = redis.set(lease_key, lease_val, nx=True, ex=lease_ttl)
                    except Exception as e:
                        logger.warning("Redis error acquiring lease for %s: %s", acct_id, e)
                        continue
                    if not ok:
                        continue  # someone else is watching

                    stop_ev = threading.Event()
                    t = threading.Thread(
                        target=_watch_account,
                        name=f"imap-idle-{acct_id}",
                        args=(acct_id, stop_ev, lease_key, lease_val, lease_ttl, idle_reissue_sec, debounce_sec),
                        daemon=True,
                    )
                    watchers[acct_id] = Watcher(
                        account_id=acct_id,
                        thread=t,
                        stop=stop_ev,
                        lease_key=lease_key,
                        lease_value=lease_val,
                        address=getattr(acct.endpoint, "address", None),
                        config_sig=_acct_config_sig(acct),
                    )
                    t.start()
                    logger.info("Started watcher for %s (endpoint=%s)", acct_id, getattr(acct.endpoint, "address", None))

                # 5) Log full active watcher list if changed
                current_keys = tuple(sorted(watchers.keys()))
                if current_keys != last_logged_keys:
                    last_logged_keys = current_keys
                    if watchers:
                        details = ", ".join(
                            f"{w.address or 'unknown'}<{aid}>" for aid, w in watchers.items()
                        )
                        logger.info("Active IMAP IDLE watchers (%d): %s", len(watchers), details)
                    else:
                        logger.info("Active IMAP IDLE watchers (0): none")

                for _ in range(scan_interval):
                    if stop_main.is_set():
                        break
                    if rescan_now.is_set():
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Shutting down IMAP IDLE runner…")
        finally:
            for w in watchers.values():
                w.stop.set()
            for w in watchers.values():
                try:
                    w.thread.join(timeout=5)
                except Exception:
                    pass
            for w in watchers.values():
                try:
                    cur = redis.get(w.lease_key)
                    if cur == w.lease_value:
                        redis.delete(w.lease_key)
                except Exception:
                    pass


def _watch_account(
    account_id: str,
    stop: threading.Event,
    lease_key: str,
    lease_val: str,
    lease_ttl: int,
    idle_reissue_sec: int,
    debounce_sec: int,
):
    """Maintain a single IDLE session for an account and enqueue poll tasks on new mail events."""
    redis = get_redis_client()
    backoff = 5  # seconds
    max_backoff = 300

    while not stop.is_set():
        acct: Optional[AgentEmailAccount] = None
        try:
            acct = AgentEmailAccount.objects.select_related("endpoint").get(pk=account_id)
        except AgentEmailAccount.DoesNotExist:
            logger.info("Account %s no longer exists; stopping watcher", account_id)
            return

        try:
            # Honor backoff if account has connection issues
            if acct.backoff_until and acct.backoff_until > timezone.now():
                sleep_for = max(1, int((acct.backoff_until - timezone.now()).total_seconds()))
                logger.info("Watcher %s backoff %ds due to account backoff_until", account_id, sleep_for)
                _sleep_until(stop, sleep_for)
                continue

            # Connect
            host = acct.imap_host
            port = int(acct.imap_port or (993 if acct.imap_security == AgentEmailAccount.ImapSecurity.SSL else 143))
            use_ssl = acct.imap_security == AgentEmailAccount.ImapSecurity.SSL
            client = IMAPClient(host, port=port, ssl=use_ssl, timeout=60)

            if acct.imap_security == AgentEmailAccount.ImapSecurity.STARTTLS:
                client.starttls()

            client.login(acct.imap_username or "", acct.get_imap_password() or "")
            folder = acct.imap_folder or "INBOX"
            client.select_folder(folder, readonly=True)

            # If server doesn't support IDLE, log and stop watcher; polling will handle new mail
            try:
                caps = {c.decode().upper() if isinstance(c, (bytes, bytearray)) else str(c).upper() for c in (client.capabilities() or [])}
            except Exception:
                caps = set()
            if "IDLE" not in caps:
                logger.info("Watcher %s: server does not support IDLE; stopping watcher (polling continues)", account_id)
                try:
                    client.logout()
                except Exception:
                    pass
                return

            logger.info("Watcher %s connected and idling on %s", account_id, folder)

            start = time.time()
            while not stop.is_set():
                # Refresh lease and verify ownership
                try:
                    cur = redis.get(lease_key)
                    if cur != lease_val:
                        logger.info("Watcher %s lost lease; exiting", account_id)
                        try:
                            client.logout()
                        except Exception:
                            pass
                        return
                    redis.expire(lease_key, lease_ttl)
                except Exception:
                    # Non-fatal; try to proceed
                    pass

                # Re-issue IDLE periodically
                elapsed = time.time() - start
                if elapsed >= idle_reissue_sec:
                    try:
                        client.idle_done()
                    except Exception:
                        pass
                    start = time.time()

                # Enter IDLE and wait for events with a short timeout to allow heartbeats
                client.idle()
                try:
                    responses = client.idle_check(timeout=5)
                except Exception:
                    responses = []
                finally:
                    try:
                        client.idle_done()
                    except Exception:
                        pass

                if stop.is_set():
                    break

                # Parse events – look for EXISTS or RECENT
                triggered = False
                for resp in responses or []:
                    # Typical forms: (b'EXISTS', 123), (b'RECENT', 1)
                    try:
                        if len(resp) >= 2:
                            typ = str(resp[0]).upper()
                            if "EXISTS" in typ or "RECENT" in typ:
                                triggered = True
                                break
                    except Exception:
                        continue

                if triggered:
                    # Debounce
                    trig_key = f"imap-trigger:{account_id}"
                    try:
                        ok = redis.set(trig_key, "1", nx=True, ex=debounce_sec)
                    except Exception:
                        ok = True  # best-effort: proceed
                    if ok:
                        try:
                            poll_imap_inbox.delay(account_id)
                        except Exception as e:
                            logger.warning("Failed to enqueue poll for %s: %s", account_id, e)

                # Small sleep to avoid hot loop when idle_check returns empty
                _sleep_until(stop, 1)

            try:
                client.logout()
            except Exception:
                pass
            return

        except Exception as e:
            logger.warning("Watcher %s IMAP error: %s", account_id, e)
            # On error, release lease so another runner can try
            try:
                cur = redis.get(lease_key)
                if cur == lease_val:
                    redis.delete(lease_key)
            except Exception:
                pass
            # Backoff and retry
            _sleep_until(stop, backoff)
            backoff = min(max_backoff, backoff * 2)
        finally:
            try:
                client  # type: ignore[name-defined]
            except NameError:
                pass


def _sleep_until(stop: threading.Event, seconds: int) -> None:
    """Sleep for up to `seconds` but wake early if stop is set."""
    end = time.time() + max(0, int(seconds))
    while not stop.is_set() and time.time() < end:
        time.sleep(0.2)
