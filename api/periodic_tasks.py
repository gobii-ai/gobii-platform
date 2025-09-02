"""
All Celery Beat schedules live here.
• Static tasks are hard-coded.
• Dynamic tasks: single nightly job that syncs all DecodoIPBlocks.
"""

from celery.schedules import crontab
from redbeat import RedBeatSchedulerEntry
from celery import current_app as celery_app
import logging

logger = logging.getLogger(__name__)

# ---------- STATIC TASKS ----------
beat_schedule: dict[str, dict] = {
    # add static tasks here as needed …
}

# ---------- DYNAMIC TASKS ----------
def add_dynamic_schedules():
    """Add nightly tasks for IP sync and proxy health checks."""
    # Add single nightly sync job that will iterate all blocks
    beat_schedule["decodo-ip-sync-daily"] = {
        "task": "gobii_platform.api.tasks.sync_all_ip_blocks",
        "schedule": crontab(hour=2, minute=0),  # 02:00 UTC daily
        "args": [],
    }
    
    # Add nightly proxy health check
    beat_schedule["proxy-health-check-nightly"] = {
        "task": "gobii_platform.api.tasks.proxy_health_check_nightly",
        "schedule": crontab(hour=3, minute=30),  # 03:30 UTC daily (after IP sync)
        "args": [],
    }

    # Add a monthly prune of UsageThresholdSent records older than 18 months
    beat_schedule["prune-threshold-sent-monthly"] = {
        "task": "prune_usage_threshold_sent",
        "schedule": crontab(hour=3, minute=0, day_of_month='1'),  # 3 AM on the 1st
        "args": [],
    }

    # Hourly soft-expiration sweep for inactive free-plan agents
    beat_schedule["agent-soft-expire-hourly"] = {
        "task": "api.agent.tasks.soft_expire_inactive_agents",
        "schedule": crontab(minute=0),  # Top of every hour UTC
        "args": [],
    }

def clean_up_old_decodo_schedules():
    """Clean up old per-block schedule entries from Redis Beat."""
    logger.info("Starting cleanup of old Decodo IP block sync schedules")
    
    import redis
    from config.redis_client import get_redis_client
    
    try:
        # Get Redis client to find existing per-block schedules from old implementation
        redis_client = get_redis_client()
        
        # Find all old per-block schedule keys
        schedule_keys = redis_client.keys("redbeat:sync-decodo-block-*")
        
        for key in schedule_keys:
            try:
                key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                if key_str.startswith("redbeat:sync-decodo-block-"):
                    # Remove old per-block schedule
                    schedule_name = key_str.replace("redbeat:", "")
                    entry = RedBeatSchedulerEntry.from_key(key, app=celery_app)
                    if entry:
                        entry.delete()
                        logger.info(f"Removed old per-block schedule: {schedule_name}")
            except Exception as e:
                logger.error(f"Failed to remove old schedule {key_str}: {e}")
                        
    except Exception as e:
        logger.error(f"Error during schedule cleanup: {e}")

# ---------- UPSERTER ----------
def sync_to_redis():
    """Idempotently upsert each entry in `beat_schedule` into Redis."""
    app = celery_app
    
    # First, clean up any orphaned schedules
    clean_up_old_decodo_schedules()
    
    # Then add/update current schedules
    add_dynamic_schedules()

    for name, spec in beat_schedule.items():
        try:
            RedBeatSchedulerEntry(
                name=name,
                task=spec["task"],
                schedule=spec["schedule"],
                args=spec.get("args", []),
                app=app,
            ).save()
            logger.info(f"Synced schedule: {name}")
        except Exception as e:
            logger.error(f"Failed to sync schedule {name}: {e}")

def cleanup_schedule_for_block(block_id: str):
    """
    Legacy function - no longer needed since we use a single nightly schedule.
    
    With the new single nightly schedule approach, individual blocks don't have
    their own schedules, so there's nothing to clean up when a block is deleted.
    """
    logger.info(f"cleanup_schedule_for_block called for {block_id} - no action needed with single nightly schedule")
