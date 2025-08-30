---
title: Using Secrets
order: 50
icon: lock
---

Keep sensitive data secure when automating tasks that require passwords, API keys, or other confidential information. Gobii encrypts your secrets at rest and ensures the AI model never sees the actual values.

## How Secrets Work

When you provide secrets to a task:

1. **Domain-specific secrets**: Secrets are organized by domain to ensure they're only used on the appropriate websites
2. **Placeholders in prompts**: Use placeholder names (like `x_username`, `x_password`) in your task description
3. **Secure encryption**: Actual secret values are encrypted using AES-256-GCM before storage
4. **Protected from AI**: The language model only sees placeholder names, never the real secrets
5. **Runtime substitution**: During browser automation, placeholders are replaced with actual values for the matching domain

## Domain-Specific Format

Secrets must be provided in a domain-specific format where you specify which domain each secret should be used on:

```json
{
  "secrets": {
    "https://example.com": {
      "x_username": "your_username", 
      "x_password": "your_password"
    },
    "https://api.service.com": {
      "x_api_key": "your_api_key"
    }
  }
}
```

This ensures your secrets are only available when the browser is on the appropriate domain, providing an additional layer of security.

## Basic Example

```bash
curl -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Login to example.com using x_username and x_password, then navigate to the dashboard",
    "secrets": {
      "https://example.com": {
        "x_username": "alice@company.com",
        "x_password": "mySecretPassword123"
      }
    }
  }'
```

The AI agent will:

- See the task: "Login to example.com using x_username and x_password..."
- **Not** see the actual username or password values
- Use the real credentials only when filling login forms

## Secret Key Requirements

Secret keys must follow these rules:

- ✅ **Alphanumeric characters and underscores only**: `x_api_key`, `user123`, `db_password`
- ✅ **Cannot start with a number**: `x_token` (good), `1_token` (bad)
- ❌ **No spaces, dashes, or special characters**: `api-key`, `my password`

## Multiple Secrets

You can provide multiple secrets for the same domain or across different domains:

```bash
curl -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Login to admin panel using x_username and x_password, then configure the API with x_api_key",
    "secrets": {
      "https://admin.example.com": {
        "x_username": "admin",
        "x_password": "admin123",
        "x_api_key": "sk-1234567890abcdef"
      }
    }
  }'
```

Or across multiple domains:

```bash
curl -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Login to site1.com using x_username and x_password, then call the API at api.service.com using x_api_key",
    "secrets": {
      "https://site1.com": {
        "x_username": "user@example.com",
        "x_password": "password123"
      },
      "https://api.service.com": {
        "x_api_key": "sk-abc123def456"
      }
    }
  }'
```

## With Structured Output

Secrets work seamlessly with output schemas:

```bash
curl -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Login to dashboard using x_username and x_password, then extract the account balance and return it as JSON",
    "secrets": {
      "https://dashboard.bank.com": {
        "x_username": "user@example.com",
        "x_password": "secretpass"
      }
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "balance": { "type": "string" },
        "currency": { "type": "string" }
      },
      "required": ["balance", "currency"]
    }
  }'
```

## Synchronous Tasks with Secrets

Use the `wait` parameter for immediate results:

```bash
curl -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Login to my account using x_email and x_password and check if there are any new notifications",
    "secrets": {
      "https://myaccount.service.com": {
        "x_email": "user@example.com",
        "x_password": "mypassword"
      }
    },
    "wait": 60
  }'
```

## Security Features

### Encryption at Rest
- All secrets are encrypted using AES-256-GCM with authenticated encryption
- Encryption keys are securely managed and rotated
- Secrets are never stored in plaintext

### AI Model Protection
- The language model never sees actual secret values
- Only placeholder names appear in the model's context
- Even if the agent reads a page containing your password, it's masked from the model

### Audit Logging
- Secret usage is logged (placeholder names only, never values)
- Track which tasks used secrets and when
- No sensitive data appears in logs

## Best Practices

### 1. Use Descriptive Placeholder Names
```json
{
  "secrets": {
    "https://admin.example.com": {
      "x_admin_username": "admin",
      "x_admin_password": "secret"
    },
    "https://api.example.com": {
      "x_api_key": "sk-123"
    },
    "https://db.example.com": {
      "x_database_password": "dbpass"
    }
  }
}
```

### 2. Minimize Secret Scope
Only include secrets that are actually needed for the specific task.

### 3. Regular Rotation
Rotate passwords and API keys regularly as part of your security practices.

### 4. Environment Variables
Store secrets in environment variables in your application:

```bash
# In your environment
ADMIN_PASSWORD="secure-password-123"
API_KEY="sk-1234567890abcdef"

# In your script
curl -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"prompt\": \"Login using x_username and x_password\",
    \"secrets\": {
      \"https://admin.example.com\": {
        \"x_username\": \"admin\",
        \"x_password\": \"$ADMIN_PASSWORD\"
      }
    }
  }"
```

## Error Handling

If your secrets are not in the required domain-specific format:

```json
{
  "secrets": ["Secrets must be in domain-specific format. Use: {'https://example.com': {'x_api_key': 'value'}} instead of: {'x_api_key': 'value'}"],
  "status": 400
}
```

If your secrets contain invalid characters or format:

```json
{
  "secrets": ["Secrets must be a dictionary"],
  "status": 400
}
```

If a secret key is invalid:

```json
{
  "secrets": ["Secret key 'invalid-key' must be alphanumeric with underscores only"],
  "status": 400
}
```

If a domain pattern is invalid:

```json
{
  "secrets": ["Invalid domain pattern 'invalid-domain'. Domain must be a valid URL pattern."],
  "status": 400
}
```

## Backwards Compatibility

Tasks without secrets continue to work exactly as before. The `secrets` field is optional and only affects tasks that explicitly use it.

---

**Next Steps**

- Try the [Quickstart guide](/docs/guides/quickstart/) with secrets
- Explore [Structured Output](/docs/guides/structured-output/) for typed results  
- Check out [Synchronous Tasks](/docs/guides/synchronous-tasks/) for immediate results 