---
title: TypeScript / JavaScript
order: 20
icon: typescript
---

**Gobii** makes it super easy to spin up `browser-use` agents in the cloud. Here's how:

## Install the Client Library

Install the Gobii TypeScript client library:

```bash
npm install @gobii-ai/client
```

## Initialize the Client

```typescript
import { Configuration, BrowserUseApi } from '@gobii-ai/client';

// Initialize with your API key
const config = new Configuration({
  headers: {
    'X-Api-Key': 'your_api_key_here'
  }
  // Optional: override the base URL if needed
  // basePath: 'https://custom-domain.com/api/v1'
});

const browserUseApi = new BrowserUseApi(config);
```

## Create an Agent

```typescript
// Create a new browser automation agent
const agent = await browserUseApi.createAgent({ 
  name: "My First Agent" 
});

console.log(`Agent created with ID: ${agent.id}`);
```

## Assign a Task

There are two ways to kick-off a task:

### 1&nbsp;·&nbsp;Fire-and-forget

```typescript
const task = await browserUseApi.assignTask(agent.id, {
  prompt: "Visit https://example.com and extract the main heading"
});
// Response contains the new task ID; poll for status later.
```

### 2&nbsp;·&nbsp;Wait inline (sync)

Add the `wait` parameter (0-900&nbsp;seconds). The request blocks **until** the task finishes or the timeout elapses.

```typescript
const task = await browserUseApi.assignTask(agent.id, {
  prompt: "Visit https://example.com and extract the main heading",
  wait: 30          // wait up to 30 s for a result
});

if (task.status === "completed") {
  console.log("Heading:", task.result);
} else {
  console.log("Still running – poll later");
}
```

Need strongly-typed JSON back? Supply an `output_schema` – see the **Structured Output** guide.

## Get Task Results

```typescript
// Poll for task completion
async function getTaskResult(agentId, taskId) {
  while (true) {
    const result = await browserUseApi.getTaskResult(agentId, taskId);
    
    if (result.status === "completed") {
      console.log("Task completed!");
      console.log("Result:", result.result);
      return result.result;
    } else if (result.status === "failed" || result.status === "cancelled") {
      console.log(`Task ${result.status}:`, result.error_message);
      return null;
    }
    
    console.log(`Task status: ${result.status}, waiting...`);
    await new Promise(resolve => setTimeout(resolve, 5000)); // Wait 5 seconds
  }
}

// Call the function to get your results
const result = await getTaskResult(agent.id, task.id);
```

## Complete Example

Here's a complete example that ties everything together:

```typescript
import { Configuration, BrowserUseApi } from '@gobii-ai/client';

async function main() {
  // Initialize client
  const api = new BrowserUseApi(new Configuration({
    headers: { 'X-Api-Key': 'your_api_key_here' }
  }));
  
  try {
    // Create agent
    const agent = await api.createAgent({ name: 'QuickStart Agent' });
    console.log(`Agent created with ID: ${agent.id}`);
    
    // Assign task
    const task = await api.assignTask(agent.id, { 
      prompt: "Visit https://example.com and extract the main heading" 
    });
    console.log(`Task assigned with ID: ${task.id}`);
    
    // Wait for result
    let attempts = 0;
    while (attempts < 20) { // Timeout after ~100 seconds
      const result = await api.getTaskResult(agent.id, task.id);
      
      if (result.status === "completed") {
        console.log("Result:", result.result);
        break;
      } else if (result.status === "failed" || result.status === "cancelled") {
        console.log(`Task ${result.status}:`, result.error_message);
        break;
      }
      
      console.log(`Status: ${result.status}...`);
      await new Promise(resolve => setTimeout(resolve, 5000));
      attempts++;
    }
  } catch (error) {
    console.error("Error:", error);
  }
}

main();
```

## Next Steps

Once you've completed this quickstart, you can:

- Learn how to make synchronous calls in&nbsp;detail – see [Synchronous Tasks](/docs/guides/synchronous-tasks/)
- Define a JSON Schema for guaranteed structure – see [Structured Output](/docs/guides/structured-output/)
- Check out the complete <a href="/api/schema/swagger-ui/" target="_blank">API Reference</a> 