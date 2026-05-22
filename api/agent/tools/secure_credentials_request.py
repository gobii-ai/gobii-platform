"""
Secure credentials request tool for persistent agents.

This tool allows agents to request credentials they need from users.
The credentials are created as PersistentAgentSecret records marked
as requested=True, which signals to the user that they need to provide
these credentials before the agent can proceed with certain tasks.
"""
import logging
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from ...models import PersistentAgent, PersistentAgentSecret
from ...services.sandbox_compute import sandbox_compute_enabled_for_agent
from ...domain_validation import DomainPatternValidator

logger = logging.getLogger(__name__)

_ENV_VAR_INFERENCE_HINTS = (
    "custom tool",
    "tool script",
    "python_exec",
    "run_command",
    "mcp server",
    "os.environ",
    "environment variable",
    "env var",
    "python snippet",
    "python code",
    "python script",
    "sandbox python",
)


def _resolve_secret_type(
    *,
    raw_secret_type,
    sandbox_enabled: bool,
    name,
    description,
    key,
) -> tuple[str, bool]:
    explicit_secret_type = str(raw_secret_type or "").strip().lower()
    if explicit_secret_type:
        return explicit_secret_type, False

    if not sandbox_enabled:
        return PersistentAgentSecret.SecretType.CREDENTIAL, False

    normalized_key = str(key or "").strip().upper()
    combined_text = " ".join(str(value or "") for value in (name, description)).lower()
    hinted_env_usage = any(hint in combined_text for hint in _ENV_VAR_INFERENCE_HINTS)
    if hinted_env_usage and PersistentAgentSecret.ENV_VAR_KEY_PATTERN.match(normalized_key):
        return PersistentAgentSecret.SecretType.ENV_VAR, True

    return PersistentAgentSecret.SecretType.CREDENTIAL, False


def get_secure_credentials_request_tool() -> dict:
    """Return the tool definition for secure credentials request."""
    return {
        "type": "function",
        "function": {
            "name": "secure_credentials_request",
            "description": (
                "Request credentials only for immediate http_request/API, spawn_web_task login, or sandbox-code use. Do not use for MCP OAuth; call the MCP tool and surface its auth link. "
                "Use secret_type='credential' with domain_pattern for website placeholders. "
                "If a custom tool script, python_exec, run_command, or MCP server reads os.environ, ALWAYS set secret_type='env_var' and omit domain_pattern. "
                "Broad domains are usually better, e.g. *.google.com. Send the returned credential-entry/update URL to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "credentials": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Human-readable name."},
                                "description": {"type": "string", "description": "Credential purpose."},
                                "key": {"type": "string", "description": "Unique key, e.g. api_key or username."},
                                "domain_pattern": {"type": "string", "description": "Required for credential; omit it for env_var."},
                                "secret_type": {
                                    "type": "string",
                                    "enum": ["credential", "env_var"],
                                    "description": "credential for domain-scoped secrets; env_var for os.environ in custom tool scripts or sandbox code. If sandbox code reads it, this MUST be env_var.",
                                },
                            },
                            "required": ["name", "description", "key"]
                        },
                        "description": "Credentials to request."
                    }
                },
                "required": ["credentials"],
            },
        },
    }


def execute_secure_credentials_request(agent: PersistentAgent, params: dict) -> dict:
    """Create secure credential requests for the agent.
    
    This tool allows agents to request credentials they need from users.
    The credentials are created as PersistentAgentSecret records marked
    as requested=True, which signals to the user that they need to provide
    these credentials before the agent can proceed with certain tasks.
    """
    credentials = params.get("credentials")
    if not credentials or not isinstance(credentials, list):
        return {"status": "error", "message": "Missing or invalid required parameter: credentials"}
    
    if not credentials:
        return {"status": "error", "message": "At least one credential must be specified"}
    
    created_credentials = []  # pending requests (new or already-requested)
    rerequested_credentials = []  # fulfilled creds we want the user to update
    errors = []

    logger.info(
        "Agent %s requesting %d credentials",
        agent.id, len(credentials)
    )

    sandbox_enabled = sandbox_compute_enabled_for_agent(agent)
    
    for cred in credentials:
        if not isinstance(cred, dict):
            errors.append(f"Invalid credential payload: {cred!r}")
            continue
        try:
            # Validate required fields
            name = cred.get("name")
            description = cred.get("description") 
            key = cred.get("key")
            secret_type, inferred_env_var = _resolve_secret_type(
                raw_secret_type=cred.get("secret_type"),
                sandbox_enabled=sandbox_enabled,
                name=name,
                description=description,
                key=key,
            )
            domain_pattern = cred.get("domain_pattern")
            
            if secret_type not in {
                PersistentAgentSecret.SecretType.CREDENTIAL,
                PersistentAgentSecret.SecretType.ENV_VAR,
            }:
                errors.append(f"Invalid secret_type for credential '{name or 'unknown'}': {secret_type}")
                continue

            if not all([name, description, key]):
                errors.append(f"Missing required fields for credential: {cred}")
                continue

            if inferred_env_var:
                logger.info(
                    "Inferring env_var secret_type for agent %s credential key=%s based on sandbox env usage hints",
                    agent.id,
                    key,
                )

            if secret_type == PersistentAgentSecret.SecretType.ENV_VAR:
                if not sandbox_enabled:
                    errors.append(
                        f"Cannot request env_var secret '{name}' because sandbox compute is not enabled for this agent."
                    )
                    continue
                normalized_domain = PersistentAgentSecret.ENV_VAR_DOMAIN_SENTINEL
                normalized_key = str(key).strip().upper()
            else:
                if not domain_pattern:
                    errors.append(
                        f"Missing required fields for credential (domain_pattern required for secret_type=credential): {cred}"
                    )
                    continue
                try:
                    DomainPatternValidator.validate_domain_pattern(str(domain_pattern))
                    normalized_domain = DomainPatternValidator.normalize_domain_pattern(str(domain_pattern))
                except ValueError as exc:
                    errors.append(f"Invalid domain pattern for credential '{name}': {exc}")
                    continue
                normalized_key = str(key).strip()

            # Check if a credential with this key already exists for this agent
            existing = PersistentAgentSecret.objects.filter(
                agent=agent, 
                key=normalized_key,
                secret_type=secret_type,
                domain_pattern=normalized_domain,
            ).first()
            
            if existing:
                if existing.requested:
                    # Already requested, skip creating another
                    logger.info(
                        "Credential %s for domain %s already requested for agent %s",
                        normalized_key, normalized_domain, agent.id
                    )
                    created_credentials.append(
                        {
                            "name": existing.name,
                            "key": existing.key,
                            "domain_pattern": existing.domain_pattern,
                            "secret_type": existing.secret_type,
                        }
                    )
                    continue

                # Fulfilled secret: ask user to update instead of wiping or toggling requested
                logger.info(
                    "Re-requesting existing credential %s for domain %s for agent %s",
                    normalized_key, normalized_domain, agent.id
                )
                rerequested_credentials.append(
                    {
                        "name": existing.name,
                        "key": existing.key,
                        "domain_pattern": existing.domain_pattern,
                        "secret_type": existing.secret_type,
                    }
                )
                continue

            # Create the credential request
            secret = PersistentAgentSecret(
                agent=agent,
                name=name,
                description=description,
                key=normalized_key,
                secret_type=secret_type,
                domain_pattern=normalized_domain,
                requested=True,
                # Use empty bytes since this is just a request and the field cannot be NULL
                encrypted_value=b'',
            )
            secret.full_clean()
            secret.save()
            
            created_credentials.append({
                "name": name,
                "key": secret.key,
                "domain_pattern": secret.domain_pattern,
                "secret_type": secret.secret_type,
            })
            
            logger.info(
                "Created credential request for agent %s: %s (%s) for domain %s",
                agent.id, name, secret.key, secret.domain_pattern
            )
            
        except (ValidationError, IntegrityError, ValueError, TypeError) as exc:
            error_msg = f"Failed to create credential request '{cred.get('name', 'unknown')}': {str(exc)}"
            errors.append(error_msg)
            logger.exception("Error creating credential request for agent %s", agent.id)
    
    # Generate the full external URL for the credentials request page
    try:
        current_site = Site.objects.get_current()
        # Use HTTPS as the default protocol based on project configuration
        protocol = 'https://'
        relative_url = f"/app/agents/{agent.id}/secrets/request"
        credentials_url = f"{protocol}{current_site.domain}{relative_url}"

        relative_secret_url = f"/app/agents/{agent.id}/secrets"
        secrets_url = f"{protocol}{current_site.domain}{relative_secret_url}"
    except Site.DoesNotExist as exc:
        logger.warning("Failed to generate credentials URL for agent %s: %s", agent.id, str(exc))
        credentials_url = "the agent console"
        secrets_url = ""
    
    total_count = len(created_credentials) + len(rerequested_credentials)

    def _format_creds(creds: list[dict]) -> str:
        return ", ".join([f"'{c['name']}' ({c['key']})" for c in creds])

    # Build response message
    if total_count and not errors:
        parts = [f"Processed {total_count} credential request(s)."]
        if created_credentials:
            parts.append(f"Pending credential request(s): {_format_creds(created_credentials)}.")
        if rerequested_credentials:
            parts.append(f"Re-requested existing credential(s): {_format_creds(rerequested_credentials)}.")

        instructions = []
        if created_credentials:
            instructions.append(f"Ask the user to securely enter the requested credentials at {credentials_url}")
        if rerequested_credentials:
            if secrets_url:
                instructions.append(f"Ask the user to update the existing credential(s) here: {secrets_url}")
            else:
                instructions.append("Ask the user to update the existing credential(s) on their agent secrets page.")

        message = " ".join(parts + instructions)
        return {"status": "ok", "message": message, "created_count": total_count}
    
    elif total_count and errors:
        error_list = "; ".join(errors)
        parts = [f"Processed {total_count} credential request(s) with errors: {_format_creds(created_credentials + rerequested_credentials)}."]

        instructions = []
        if created_credentials:
            instructions.append(f"Ask the user to securely enter the requested credentials at {credentials_url}.")
        if rerequested_credentials:
            if secrets_url:
                instructions.append(f"Ask the user to update the existing credential(s) here: {secrets_url}.")
            else:
                instructions.append("Ask the user to update the existing credential(s) on their agent secrets page.")

        message = " ".join(parts + instructions + [f"Errors: {error_list}"])
        return {"status": "partial", "message": message, "created_count": total_count, "errors": errors}
    
    else:
        error_list = "; ".join(errors) if errors else "Unknown error occurred"
        return {"status": "error", "message": f"Failed to create any credential requests. Errors: {error_list}"} 
