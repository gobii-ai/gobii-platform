"""
Secure credentials request tool for persistent agents.

This tool allows agents to request credentials they need from users.
The credentials are created as PersistentAgentSecret records marked
as requested=True, which signals to the user that they need to provide
these credentials before the agent can proceed with certain tasks.
"""
import logging
from django.contrib.sites.models import Site
from django.urls import reverse
from ...models import PersistentAgent, PersistentAgentSecret

logger = logging.getLogger(__name__)


def get_secure_credentials_request_tool() -> dict:
    """Return the tool definition for secure credentials request."""
    return {
        "type": "function",
        "function": {
            "name": "secure_credentials_request",
            "description": (
                "Request secure credentials from the user ONLY when you will IMMEDIATELY use them with `http_request` (API keys/tokens) "
                "or `spawn_web_task` (classic username/password website login). Do NOT use this tool for MCP tools (e.g. Slack); "
                "for MCP tools, call the tool first—if it returns 'action_required' with a connect/auth link, surface that link to the user and wait. "
                "You typically will want the domain to be broad enough to support multiple login domains, e.g. *.google.com, or *.reddit.com instead of ads.reddit.com. "
                "IT WILL RETURN URL(S). ALWAYS MESSAGE THE USER WITH THE CORRECT ONE: "
                "- For new/pending requests, send the credentials-request URL so they can enter the requested secret(s). "
                "- For re-requests of existing credentials, use the update/secrets URL so they can update the existing secret value. "
                "Be explicit about which action you need (enter new vs update existing)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "credentials": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Human-readable name for the credential."},
                                "description": {"type": "string", "description": "Description of what this credential is used for."},
                                "key": {"type": "string", "description": "Unique key identifier for this credential (e.g., 'api_key', 'username')."},
                                "domain_pattern": {"type": "string", "description": "Domain pattern this credential applies to (e.g., 'api.example.com', '*.example.com')."}
                            },
                            "required": ["name", "description", "key", "domain_pattern"]
                        },
                        "description": "List of credentials to request from the user."
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
    
    for cred in credentials:
        try:
            # Validate required fields
            name = cred.get("name")
            description = cred.get("description") 
            key = cred.get("key")
            domain_pattern = cred.get("domain_pattern")
            
            if not all([name, description, key, domain_pattern]):
                errors.append(f"Missing required fields for credential: {cred}")
                continue
            
            # Check if a credential with this key already exists for this agent
            existing = PersistentAgentSecret.objects.filter(
                agent=agent, 
                key=key, 
                domain_pattern=domain_pattern
            ).first()
            
            if existing:
                if existing.requested:
                    # Already requested, skip creating another
                    logger.info(
                        "Credential %s for domain %s already requested for agent %s",
                        key, domain_pattern, agent.id
                    )
                    created_credentials.append(
                        {
                            "name": existing.name,
                            "key": existing.key,
                            "domain_pattern": existing.domain_pattern,
                        }
                    )
                    continue

                # Fulfilled secret: ask user to update instead of wiping or toggling requested
                logger.info(
                    "Re-requesting existing credential %s for domain %s for agent %s",
                    key, domain_pattern, agent.id
                )
                rerequested_credentials.append(
                    {
                        "name": existing.name,
                        "key": existing.key,
                        "domain_pattern": existing.domain_pattern,
                    }
                )
                continue

            # Create the credential request
            secret = PersistentAgentSecret.objects.create(
                agent=agent,
                name=name,
                description=description,
                key=key,
                domain_pattern=domain_pattern,
                requested=True,
                # Use empty bytes since this is just a request and the field cannot be NULL
                encrypted_value=b''
            )
            
            created_credentials.append({
                "name": name,
                "key": key,
                "domain_pattern": domain_pattern
            })
            
            logger.info(
                "Created credential request for agent %s: %s (%s) for domain %s",
                agent.id, name, key, domain_pattern
            )
            
        except Exception as e:
            error_msg = f"Failed to create credential request '{cred.get('name', 'unknown')}': {str(e)}"
            errors.append(error_msg)
            logger.exception("Error creating credential request for agent %s", agent.id)
    
    # Generate the full external URL for the credentials request page
    try:
        current_site = Site.objects.get_current()
        # Use HTTPS as the default protocol based on project configuration
        protocol = 'https://'
        relative_url = reverse('agent_secrets_request', kwargs={'pk': agent.id})
        credentials_url = f"{protocol}{current_site.domain}{relative_url}"

        relative_secret_url = reverse('agent_secrets', kwargs={'pk': agent.id})
        secrets_url = f"{protocol}{current_site.domain}{relative_secret_url}"
    except Exception as e:
        logger.warning("Failed to generate credentials URL for agent %s: %s", agent.id, str(e))
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
