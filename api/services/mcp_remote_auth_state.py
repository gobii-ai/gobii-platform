import json
import logging
from typing import Any, Dict, Optional

from django.db import DatabaseError, transaction

from api.encryption import SecretsEncryption
from api.models import MCPServerConfig, MCPServerOAuthCredential


logger = logging.getLogger(__name__)

_MAX_REMOTE_AUTH_STATE_BYTES = 512 * 1024


def _credential_defaults_for_config(config: MCPServerConfig) -> Dict[str, Any]:
    return {
        "organization": config.organization if config.scope == MCPServerConfig.Scope.ORGANIZATION else None,
        "user": config.user if config.scope == MCPServerConfig.Scope.USER else None,
    }


def load_remote_auth_state(config: MCPServerConfig) -> Optional[Dict[str, Any]]:
    try:
        credential = config.oauth_credential
    except MCPServerOAuthCredential.DoesNotExist:
        return None

    payload = credential.remote_auth_state_encrypted
    if not payload:
        return None

    try:
        raw = SecretsEncryption.decrypt_value(payload)
        parsed = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        logger.exception("Failed to decrypt remote MCP auth state for server %s", config.id)
        return None

    if not isinstance(parsed, dict):
        logger.warning("Remote MCP auth state for server %s is not a JSON object", config.id)
        return None
    return parsed


def store_remote_auth_state(config: MCPServerConfig, state: Dict[str, Any]) -> bool:
    if not isinstance(state, dict) or not state:
        return False

    try:
        raw = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        logger.warning("Remote MCP auth state for server %s is not serializable", config.id)
        return False

    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) > _MAX_REMOTE_AUTH_STATE_BYTES:
        logger.warning(
            "Remote MCP auth state too large for server %s size=%s max=%s",
            config.id,
            len(raw_bytes),
            _MAX_REMOTE_AUTH_STATE_BYTES,
        )
        return False

    try:
        encrypted = SecretsEncryption.encrypt_value(raw)
    except ValueError:
        logger.exception("Failed to encrypt remote MCP auth state for server %s", config.id)
        return False

    try:
        with transaction.atomic():
            credential, _created = MCPServerOAuthCredential.objects.select_for_update().get_or_create(
                server_config=config,
                defaults=_credential_defaults_for_config(config),
            )
            if credential.remote_auth_state_encrypted == encrypted:
                return True

            update_fields = ["remote_auth_state_encrypted", "updated_at"]
            credential.remote_auth_state_encrypted = encrypted

            if credential.organization_id is None and config.scope == MCPServerConfig.Scope.ORGANIZATION:
                credential.organization = config.organization
                update_fields.append("organization")
            if credential.user_id is None and config.scope == MCPServerConfig.Scope.USER:
                credential.user = config.user
                update_fields.append("user")

            credential.save(update_fields=list(dict.fromkeys(update_fields)))
        return True
    except DatabaseError:
        logger.exception("Failed to persist remote MCP auth state for server %s", config.id)
    return False


def pop_remote_auth_state(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    state = payload.pop("remote_auth_state", None)
    if isinstance(state, dict):
        return state
    return None
