# Generated migration for converting secrets to domain-specific format

import json
import logging
from django.db import migrations

logger = logging.getLogger(__name__)

def convert_secrets_to_domain_format(apps, schema_editor):
    """
    Convert existing flat secrets format to domain-specific format.
    
    Legacy format: {"key": "value"}
    New format: {"https://example.com": {"key": "value"}}
    
    For backwards compatibility, we'll put all legacy secrets under a generic domain.
    """
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    BrowserUseAgentTask = apps.get_model("api", "BrowserUseAgentTask")
    
    # We can't import the encryption class directly in migrations, so we'll work with decrypted data
    # that's already been stored in the database. This assumes the migration runs while the system
    # is down or in a controlled environment.
    
    # Default domain for legacy secrets
    DEFAULT_DOMAIN = "https://*.legacy-migrated.local"
    
    converted_agents = 0
    converted_tasks = 0
    
    # Convert PersistentAgent secrets
    for agent in PersistentAgent.objects.filter(encrypted_secrets__isnull=False):
        try:
            if agent.secret_keys and isinstance(agent.secret_keys, list):
                # This indicates legacy format where secret_keys was a list of key names
                # We'll convert the audit trail to the new format
                new_secret_keys = {DEFAULT_DOMAIN: agent.secret_keys}
                agent.secret_keys = new_secret_keys
                agent.save(update_fields=['secret_keys'])
                converted_agents += 1
                logger.info(f"Converted PersistentAgent {agent.id} secret_keys to domain format")
        except Exception as e:
            logger.error(f"Failed to convert PersistentAgent {agent.id}: {str(e)}")
    
    # Convert BrowserUseAgentTask secrets
    for task in BrowserUseAgentTask.objects.filter(encrypted_secrets__isnull=False):
        try:
            if task.secret_keys and isinstance(task.secret_keys, list):
                # This indicates legacy format where secret_keys was a list of key names
                # We'll convert the audit trail to the new format
                new_secret_keys = {DEFAULT_DOMAIN: task.secret_keys}
                task.secret_keys = new_secret_keys
                task.save(update_fields=['secret_keys'])
                converted_tasks += 1
                logger.info(f"Converted BrowserUseAgentTask {task.id} secret_keys to domain format")
        except Exception as e:
            logger.error(f"Failed to convert BrowserUseAgentTask {task.id}: {str(e)}")
    
    logger.info(f"Migration completed: converted {converted_agents} agents and {converted_tasks} tasks")

def reverse_conversion(apps, schema_editor):
    """
    Reverse the conversion (for rollback).
    """
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    BrowserUseAgentTask = apps.get_model("api", "BrowserUseAgentTask")
    
    DEFAULT_DOMAIN = "https://*.legacy-migrated.local"
    
    # Reverse PersistentAgent secrets
    for agent in PersistentAgent.objects.filter(encrypted_secrets__isnull=False):
        try:
            if (agent.secret_keys and isinstance(agent.secret_keys, dict) and 
                DEFAULT_DOMAIN in agent.secret_keys):
                # Convert back to list format
                agent.secret_keys = agent.secret_keys[DEFAULT_DOMAIN]
                agent.save(update_fields=['secret_keys'])
        except Exception as e:
            logger.error(f"Failed to reverse PersistentAgent {agent.id}: {str(e)}")
    
    # Reverse BrowserUseAgentTask secrets
    for task in BrowserUseAgentTask.objects.filter(encrypted_secrets__isnull=False):
        try:
            if (task.secret_keys and isinstance(task.secret_keys, dict) and 
                DEFAULT_DOMAIN in task.secret_keys):
                # Convert back to list format
                task.secret_keys = task.secret_keys[DEFAULT_DOMAIN]
                task.save(update_fields=['secret_keys'])
        except Exception as e:
            logger.error(f"Failed to reverse BrowserUseAgentTask {task.id}: {str(e)}")

class Migration(migrations.Migration):

    dependencies = [
        ('api', '0060_persistentagent_encrypted_secrets_and_more'),
    ]

    operations = [
        migrations.RunPython(
            convert_secrets_to_domain_format,
            reverse_conversion,
        ),
    ] 