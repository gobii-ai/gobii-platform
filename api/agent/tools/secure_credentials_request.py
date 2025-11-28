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
                "or `spawn_web_task` (classic username/password website login). Do NOT use this tool for MCP tools (e.g., Google Sheets, Slack); "
                "for MCP tools, call the tool first—if it returns 'action_required' with a connect/auth link, surface that link to the user and wait. "
                "You typically will want the domain to be broad enough to support multiple login domains, e.g. *.google.com, or *.reddit.com instead of ads.reddit.com. "
                "IT WILL RETURN A URL; YOU MUST CONTACT THE USER WITH THAT URL SO THEY KNOW THE REQUEST HAS BEEN CREATED AND THEY CAN FILL IN THE SECRETS/CREDENTIALS. "
                "IF YOU ARE RE-REQUESTING CREDENTIALS DUE TO AN ERROR, MAKE SURE YOU USE THE RE-REQUEST URL PROVIDED IN THE RETURNED STRING"
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
    
    created_credentials = []
    errors = []
    rerequested_credentials = False
    
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
                else:
                    logger.info(
                        "Re-requesting existing credential %s for domain %s for agent %s",
                        key, domain_pattern, agent.id
                    )
                    rerequested_credentials = True

                # Treat as created for user feedback
                created_credentials.append({
                    "name": existing.name,
                    "key": existing.key,
                    "domain_pattern": existing.domain_pattern,
                })
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

        relative_secret_url = reverse('agent_secret', kwargs={'pk': agent.id})
        secrets_url = f"{protocol}{current_site.domain}{relative_secret_url}"
    except Exception as e:
        logger.warning("Failed to generate credentials URL for agent %s: %s", agent.id, str(e))
        credentials_url = "the agent console"
        secrets_url = ""
    
    # Build response message
    if created_credentials and not errors:
        credential_list = ", ".join([f"'{c['name']}' ({c['key']})" for c in created_credentials])
        message = (
            f"Successfully created {len(created_credentials)} credential request(s): {credential_list}. "
            f"You must now send a message to the user asking them to securely enter the requested credentials at {credentials_url}"
        )

        if rerequested_credentials:
            message += f" For re-request of existing credentials, they should be updated here: {secrets_url}."

        return {"status": "ok", "message": message, "created_count": len(created_credentials)}
    
    elif created_credentials and errors:
        credential_list = ", ".join([f"'{c['name']}' ({c['key']})" for c in created_credentials])
        error_list = "; ".join(errors)
        message = (
            f"Created {len(created_credentials)} credential request(s): {credential_list}. "
            f"You must now send a message to the user asking them to securely enter the requested credentials at {credentials_url}. "
            f"Errors: {error_list}"
        )

        if rerequested_credentials:
            message += f" For re-request of existing credentials, they should be updated here: {secrets_url}."

        return {"status": "partial", "message": message, "created_count": len(created_credentials), "errors": errors}
    
    else:
        error_list = "; ".join(errors) if errors else "Unknown error occurred"
        return {"status": "error", "message": f"Failed to create any credential requests. Errors: {error_list}"} 
