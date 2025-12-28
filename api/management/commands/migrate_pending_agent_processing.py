import logging
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pottery import Redlock

from api.agent.core.processing_flags import (
    claim_pending_drain_slot,
    enqueue_pending_agent,
    get_pending_drain_settings,
    is_agent_pending,
    pending_drain_schedule_key,
    pending_set_key,
)
from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Migrate legacy per-agent pending keys into the shared pending set."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report how many legacy keys would be migrated.",
        )
        parser.add_argument(
            "--schedule-drain",
            action="store_true",
            help="Schedule a pending-drain task after migration.",
        )
        parser.add_argument(
            "--agent-id",
            help="Limit stale lock clearing to a specific agent UUID.",
        )
        parser.add_argument(
            "--clear-stale-locks",
            action="store_true",
            help="Delete stale agent event-processing locks with long TTLs.",
        )
        parser.add_argument(
            "--stale-lock-ttl-seconds",
            type=int,
            default=None,
            help=(
                "Treat locks with TTL greater than this value as stale. "
                "Defaults to the smaller of 4x the lock timeout or the pending set TTL."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        schedule_drain = options["schedule_drain"]
        agent_id_raw = options.get("agent_id")
        clear_stale_locks = options["clear_stale_locks"]
        stale_lock_ttl_seconds = options["stale_lock_ttl_seconds"]

        redis_client = get_redis_client()
        pending_settings = get_pending_drain_settings()
        lock_timeout_seconds = int(
            getattr(settings, "AGENT_EVENT_PROCESSING_LOCK_TIMEOUT_SECONDS", 900)
        )
        if stale_lock_ttl_seconds is None:
            stale_lock_ttl_seconds = max(
                1,
                min(
                    lock_timeout_seconds * 4,
                    pending_settings.pending_set_ttl_seconds,
                ),
            )

        base_key = pending_set_key()
        pattern = f"{base_key}:*"
        scan_iter = getattr(redis_client, "scan_iter", None)
        if scan_iter:
            keys = scan_iter(match=pattern)
        else:
            keys = redis_client.keys(pattern)

        found = 0
        migrated = 0
        failed = 0

        for key in keys:
            key_str = key.decode("utf-8") if isinstance(key, (bytes, bytearray)) else str(key)
            if not key_str.startswith(f"{base_key}:"):
                continue
            if key_str == pending_drain_schedule_key():
                continue
            found += 1
            agent_id = key_str.rsplit(":", 1)[-1]
            try:
                UUID(agent_id)
            except (ValueError, TypeError):
                if not dry_run:
                    redis_client.delete(key_str)
                failed += 1
                continue
            if dry_run:
                continue

            enqueue_pending_agent(
                agent_id,
                ttl=pending_settings.pending_set_ttl_seconds,
                client=redis_client,
            )
            if is_agent_pending(agent_id, client=redis_client):
                redis_client.delete(key_str)
                migrated += 1
            else:
                failed += 1

        scheduled = False
        if not dry_run and schedule_drain and migrated > 0:
            if claim_pending_drain_slot(
                ttl=pending_settings.pending_drain_schedule_ttl_seconds,
                client=redis_client,
            ):
                from api.agent.tasks.process_events import process_pending_agent_events_task

                process_pending_agent_events_task.apply_async(
                    countdown=pending_settings.pending_drain_delay_seconds,
                )
                scheduled = True

        stale_scanned = 0
        stale_cleared = 0
        if clear_stale_locks or agent_id_raw:
            prefix_name = getattr(Redlock, "_KEY_PREFIX", "redlock")
            lock_prefixes = (
                f"{prefix_name}:agent-event-processing:",
                "agent-event-processing:",
            )

            def should_clear_lock(ttl: int | None) -> bool:
                if ttl is None or ttl == -2:
                    return False
                return ttl == -1 or ttl > stale_lock_ttl_seconds

            if agent_id_raw:
                try:
                    agent_id = str(UUID(agent_id_raw))
                except (ValueError, TypeError) as exc:
                    raise CommandError(f"Invalid agent id '{agent_id_raw}'.") from exc
                for prefix in lock_prefixes:
                    key_str = f"{prefix}{agent_id}"
                    ttl = redis_client.ttl(key_str)
                    if ttl is None or ttl == -2:
                        continue
                    stale_scanned += 1
                    if not should_clear_lock(ttl):
                        continue
                    if not dry_run:
                        redis_client.delete(key_str)
                    stale_cleared += 1
            else:
                lock_scan_iter = getattr(redis_client, "scan_iter", None)
                seen_keys: set[str] = set()
                for prefix in lock_prefixes:
                    lock_pattern = f"{prefix}*"
                    if lock_scan_iter:
                        lock_keys = lock_scan_iter(match=lock_pattern)
                    else:
                        lock_keys = redis_client.keys(lock_pattern)
                    for key in lock_keys:
                        key_str = (
                            key.decode("utf-8")
                            if isinstance(key, (bytes, bytearray))
                            else str(key)
                        )
                        if key_str in seen_keys:
                            continue
                        seen_keys.add(key_str)
                        matched_prefix = next(
                            (p for p in lock_prefixes if key_str.startswith(p)),
                            None,
                        )
                        if not matched_prefix:
                            continue
                        suffix = key_str[len(matched_prefix):]
                        try:
                            UUID(suffix)
                        except (ValueError, TypeError):
                            continue
                        stale_scanned += 1
                        ttl = redis_client.ttl(key_str)
                        if not should_clear_lock(ttl):
                            continue
                        if not dry_run:
                            redis_client.delete(key_str)
                        stale_cleared += 1

        summary = (
            "Pending migration complete. "
            f"found={found} migrated={migrated} failed={failed} "
            f"dry_run={dry_run} scheduled_drain={scheduled} "
            f"stale_locks_scanned={stale_scanned} stale_locks_cleared={stale_cleared}"
        )
        if dry_run or failed == 0:
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.WARNING(summary))
        logger.info(summary)
