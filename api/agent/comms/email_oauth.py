import logging
from datetime import timedelta
from typing import Optional

import requests
from django.utils import timezone

from api.models import AgentEmailAccount, AgentEmailOAuthCredential


logger = logging.getLogger(__name__)

OAUTH_REFRESH_SAFETY_MARGIN = timedelta(minutes=2)
OAUTH_REFRESH_TIMEOUT_SECONDS = 15


def build_xoauth2_string(identity: str, access_token: str, vendor: Optional[str] = None) -> str:
    auth_string = f"user={identity}\x01auth=Bearer {access_token}\x01"
    if vendor:
        auth_string += f"vendor={vendor}\x01"
    auth_string += "\x01"
    return auth_string


def resolve_oauth_identity(account: AgentEmailAccount, channel: str) -> str:
    if channel == "smtp":
        return account.smtp_username or account.endpoint.address
    if channel == "imap":
        return account.imap_username or account.endpoint.address
    return account.endpoint.address


def resolve_oauth_identity_and_token(
    account: AgentEmailAccount,
    channel: str,
) -> tuple[str, str, AgentEmailOAuthCredential]:
    credential = get_email_oauth_credential(account)
    if not credential or not credential.access_token:
        raise RuntimeError(f"OAuth access token missing for {channel.upper()} account")
    identity = resolve_oauth_identity(account, channel)
    return identity, credential.access_token, credential


def get_email_oauth_credential(account: AgentEmailAccount) -> Optional[AgentEmailOAuthCredential]:
    try:
        credential = account.oauth_credential
    except AgentEmailOAuthCredential.DoesNotExist:
        return None

    return _maybe_refresh_email_oauth_credential(credential)


def get_oauth_sasl_mechanism(credential: AgentEmailOAuthCredential) -> str:
    metadata = credential.metadata if isinstance(credential.metadata, dict) else {}
    mechanism = str(metadata.get("sasl_mechanism") or "XOAUTH2").strip()
    return mechanism or "XOAUTH2"


def _maybe_refresh_email_oauth_credential(
    credential: AgentEmailOAuthCredential,
) -> AgentEmailOAuthCredential:
    refresh_token = (credential.refresh_token or "").strip()
    if not refresh_token:
        return credential

    expires_at = credential.expires_at
    now = timezone.now()
    if expires_at and expires_at > now + OAUTH_REFRESH_SAFETY_MARGIN:
        return credential

    metadata = credential.metadata if isinstance(credential.metadata, dict) else {}
    token_endpoint = (metadata.get("token_endpoint") or "").strip()
    if not token_endpoint:
        logger.warning(
            "Email OAuth credential for account %s lacks a token endpoint; skipping refresh",
            credential.account_id,
        )
        return credential

    request_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    client_id = (credential.client_id or metadata.get("client_id") or "").strip()
    if client_id:
        request_data["client_id"] = client_id

    client_secret = (credential.client_secret or metadata.get("client_secret") or "").strip()
    if client_secret:
        request_data["client_secret"] = client_secret

    try:
        response = requests.post(
            token_endpoint,
            data=request_data,
            timeout=OAUTH_REFRESH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.error(
            "Failed to refresh email OAuth token for account %s: %s",
            credential.account_id,
            exc,
        )
        return credential

    try:
        token_payload = response.json()
    except ValueError:
        logger.error(
            "Token refresh response for email account %s was not valid JSON",
            credential.account_id,
        )
        return credential

    new_access_token = (token_payload.get("access_token") or "").strip()
    if not new_access_token:
        logger.error(
            "Token refresh for email account %s did not return an access token",
            credential.account_id,
        )
        return credential

    update_fields = ["access_token_encrypted"]
    credential.access_token = new_access_token

    new_refresh_token = (token_payload.get("refresh_token") or "").strip()
    if new_refresh_token:
        credential.refresh_token = new_refresh_token
        update_fields.append("refresh_token_encrypted")

    new_id_token = (token_payload.get("id_token") or "").strip()
    if new_id_token:
        credential.id_token = new_id_token
        update_fields.append("id_token_encrypted")

    token_type = (token_payload.get("token_type") or "").strip()
    if token_type:
        credential.token_type = token_type
        update_fields.append("token_type")

    scope = (token_payload.get("scope") or "").strip()
    if scope:
        credential.scope = scope
        update_fields.append("scope")

    expires_in_raw = token_payload.get("expires_in")
    if expires_in_raw is not None:
        try:
            expires_seconds = int(expires_in_raw)
            credential.expires_at = now + timedelta(seconds=max(expires_seconds, 0))
        except (TypeError, ValueError):
            credential.expires_at = None
        update_fields.append("expires_at")

    metadata_update = dict(metadata)
    metadata_update["last_refresh_response"] = {
        key: value
        for key, value in token_payload.items()
        if key not in {"access_token", "refresh_token", "id_token"}
    }
    credential.metadata = metadata_update
    update_fields.append("metadata")

    credential.save(update_fields=list(dict.fromkeys(update_fields)))
    credential.refresh_from_db()
    logger.info(
        "Refreshed email OAuth token for account %s (credential updated at %s)",
        credential.account_id,
        credential.updated_at,
    )
    return credential
