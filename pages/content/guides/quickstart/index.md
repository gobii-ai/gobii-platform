---
title: Quickstart
order: 10
icon: guide
---

Four bite-sized `curl` recipes that cover the most common use-cases:

## 1 · Synchronous call (wait for result)

```bash
curl -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "prompt": "Open https://quotes.toscrape.com/ and return the text and author of the first quote on the page",
        "wait": 300
      }'
```

A quick page like example.com finishes in a few seconds:

```jsonc
{
  "id": "4801…",
  "status": "completed",
  "result": "“The world as we have created it is a process of our thinking.” — Albert Einstein"
}
```

If the 30-second window is exceeded you'll get `"status": "in_progress"` instead.

---

## 2 · Structured JSON output

```bash
curl -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "prompt": "Go to https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html and return the book title and price as JSON",
        "output_schema": {
          "type": "object",
          "properties": {
            "title":  { "type": "string" },
            "price":  { "type": "string" }
          },
          "required": ["title", "price"],
          "additionalProperties": false
        },
        "wait": 300
      }'
```

Sample response:

```jsonc
{
  "status": "completed",
  "result": {
    "title": "A Light in the Attic",
    "price": "£51.77"
  }
}
```

If the agent's answer doesn't validate against your schema the task will return `"status": "failed"` along with an `error_message` that explains why.

---

## 3 · Using secrets securely

```bash
curl -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "prompt": "Login to https://example.com using x_username and x_password, then navigate to the dashboard and check for notifications",
        "secrets": {
          "https://example.com": {
            "x_username": "alice@company.com",
            "x_password": "mySecretPassword123"
          }
        },
        "wait": 120
      }'
```

The AI agent will see only the placeholders (`x_username`, `x_password`) but use the actual credentials when needed. Your secrets are encrypted at rest and never exposed to the language model.

Sample response:

```jsonc
{
  "status": "completed",
  "result": "Successfully logged in. Found 3 new notifications in the dashboard."
}
```

---

## 4 · Async workflow (fire-and-forget)

Start the task (no `wait`):

```bash
RESP=$(curl -s -X POST https://gobii.ai/api/v1/tasks/browser-use/ \
          -H "X-Api-Key: $GOBII_API_KEY" \
          -H "Content-Type: application/json" \
          -d '{ "prompt": "Visit https://quotes.toscrape.com/tag/inspirational/ and list the first 5 inspirational quotes with their authors" }')
TASK_ID=$(echo "$RESP" | jq -r .id)
```

Check on it later:

```bash
curl -X GET https://gobii.ai/api/v1/tasks/browser-use/$TASK_ID/result/ \
     -H "X-Api-Key: $GOBII_API_KEY"
```
