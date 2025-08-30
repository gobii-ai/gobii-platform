---
title: Synchronous Tasks
order: 35
icon: stopwatch
---

Waiting for short-running jobs right inside the HTTP call can simplify your workflow—no polling loop, no extra queues.  Gobii lets you do that with the **`wait`** parameter.

## Why wait?

* Immediate results for tasks that usually finish in a few seconds
* Easier to integrate in serverless / request–response environments
* No additional billing or limitations beyond the regular task quotas

## How it works

1. Submit a task and include a `wait` field (0–1350 seconds).
2. The request thread blocks while the task is executed by our Celery worker.
3. The response you receive depends on how quickly the task finishes:
   * **completed** – full `result` payload
   * **failed** – `error_message`
   * **in_progress** – still running, keep polling the normal `/result/` endpoint

> The task itself continues even if the timeout expires.

## API example (curl)

```bash
curl --no-buffer \
  -X POST \
  https://gobii.ai/api/v1/tasks/browser-use/ \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "prompt": "Search for the latest AI news on HackerNews and return the top 3 headlines",
        "wait": 60
      }'
```

### Possible responses

```jsonc
// completed (within 45 s)
{
  "id": "...",
  "status": "completed",
  "result": { "h1_count": 1 },
  "agent_id": null
}

// still running after 45 s
{
  "id": "...",
  "status": "in_progress",
  "agent_id": null
}
```

## Using the TypeScript client

```ts
const task = await api.assignTask(agent.id, {
  prompt: "Scrape the main heading",
  wait: 30,
});

if (task.status === "completed") {
  console.log(task.result);
} else {
  console.log(`Status: ${task.status}`);
}
```

## Tips & caveats

* `wait` should be set conservatively—remember your HTTP client & gateway timeouts.
* Synchronous waiting does **not** guarantee success; always handle the `in_progress` case.
* Large `wait` values will tie up a worker thread in your application server; consider asynchronous patterns for long-running jobs.

## Next steps

Need predictable JSON output? Continue to our [Structured Output](guides/structured-output) guide. 