---
title: Structured Output
order: 40
icon: json
---

Sometimes a free-form answer isn't enough—you want the agent to give you a **well-defined JSON object** every time.  Gobii supports this via the `output_schema` field.

## Why use an output schema?

* Eliminate fragile post-processing and regex parsing
* Get compile-time types in TypeScript / Python
* Detect bad agent answers automatically (task will fail if it doesn't validate)

## Providing a schema

`output_schema` must be a [JSON Schema 2020-12](https://json-schema.org/) object.  We currently cap complexity at:

* Depth ≤ 40
* Total properties/elements ≤ 2 000

### Minimal example

```jsonc
{
  "type": "object",
  "properties": {
    "title": { "type": "string" },
    "price": { "type": "number" }
  },
  "required": ["title", "price"],
  "additionalProperties": false
}
```

## Creating a task with a schema (curl)

```bash
curl --no-buffer \
  -X POST \
  https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "prompt": "Visit https://news.ycombinator.com and extract the top 3 stories",
        "output_schema": { ... see above ... },
        "wait": 60
      }'
```

If the agent returns a value that doesn't match the schema, the task status will be **failed** and `error_message` will explain why.

## TypeScript bonus

You can derive static types from your schema:

```ts
import { FromSchema } from "json-schema-to-ts";
import schema from "./product.schema.json";

type Product = FromSchema<typeof schema>;

// Later: task.result is Product!
```

## Common error scenarios

| Scenario | What happens |
|----------|--------------|
| Invalid schema in request | HTTP 400 from the API |
| Schema too deep/large | HTTP 400 with validation error |
| Agent returns wrong shape | Task `status = failed`; check `error_message` |

## Next steps

Read about time-boxed synchronous execution in our [Synchronous Tasks](guides/synchronous-tasks) guide. 