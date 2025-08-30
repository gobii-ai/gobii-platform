# Gobii TypeScript Example API Client

This directory contains an example command-line client written in TypeScript to demonstrate interaction with the Gobii API using the auto-generated `@gobii-ai/client` package.

## Prerequisites

- Node.js (v18 or later recommended)
- npm (usually included with Node.js)
- A valid Gobii API Key

## Setup

1.  **Install Dependencies:** Navigate to this directory (`ts-example-client`) in your terminal and run:

    ```bash
    npm install
    ```

    This will install the `@gobii-ai/client`, `commander`, `dotenv`, `uuid`, and necessary development dependencies.

2.  **Configure API Key:** Create a file named `.env` in this directory (`ts-example-client/.env`) and add your API key:

    ```dotenv
    GOBII_API_KEY=your_actual_api_key_here
    ```

3.  **Build the Client:** Compile the TypeScript code:
    ```bash
    npm run build
    ```
    This creates the JavaScript output in the `dist/` directory.

## Usage

You can run the client using `npm start -- [command] [options/arguments]` or directly during development using `npm run dev -- [command] [options/arguments]`.

**Global Options:**

*   `-k, --api-key <key>`: Gobii API Key (defaults to value in `.env`)
*   `-b, --base-url <url>`: API Base URL (defaults to `http://127.0.0.1:8000/api/v1`)

**Available Commands:**

Use `npm start -- --help` to see all commands and their specific arguments.

*   `create-agent <name>`: Create a new agent.
*   `list-agents`: List all agents.
*   `get-agent <agentId>`: Get details for a specific agent.
*   `update-agent <agentId> <newName>`: Update the name of an agent.
*   `delete-agent <agentId>`: Delete an agent.
*   `assign-task <agentId> <inputData>`: Assign a task to an agent.
*   `list-tasks <agentId>`: List tasks for a specific agent.
*   `list-all-tasks`: List all tasks for the authenticated user.
*   `get-task <agentId> <taskId>`: Get details for a specific task.
*   `update-task <agentId> <taskId> <newInputData>`: Update the input data for a task.
*   `cancel-task <agentId> <taskId>`: Cancel a specific task.
*   `delete-task <agentId> <taskId>`: Delete a specific task.
*   `run-all`: Run a sequence of demo operations (create, list, assign, get, update, cancel/delete, cleanup).

**Examples:**

```bash
# List all agents using API key from .env
npm start -- --list-agents

# Create an agent named "MyTSAgent" using a specific base URL
npm start -- --base-url http://localhost:8000/api/v1 create-agent MyTSAgent

# Run the full demo sequence
npm start -- --run-all

# Run list-agents during development with tsx (no build needed)
npm run dev -- --list-agents
```

## Client Generation

This client uses a TypeScript API client generated from the Gobii API OpenAPI schema. To regenerate the client, use the provided script:

```bash
./generate-client.sh
```

This will:
1. Call the main generation script in `gobii_platform/scripts/generate-ts-client.sh`
2. Generate the latest OpenAPI schema from the Django backend
3. Use OpenAPI Generator to create the TypeScript client in `src/generated`

After regenerating, rebuild the client with `npm run build`.

> Note: The same generation script is used in CI to ensure consistency between local development and published packages.

## Notes

*   The TypeScript client provides full type safety and autocompletion.
*   All methods return Promises and should be used with async/await or .then().
*   Error handling should be implemented for production use.
*   The client uses the default base URL `https://gobii.ai/api/v1` which can be overridden if needed.

## API Structure

The generated API uses a nested parameter structure:

```typescript
// Creating an agent
browserUseApi.createAgent({
  agentCreateRequest: { name: "WebAutomationAgent" }
});

// Updating an agent
browserUseApi.updateAgent({
  id: "agent-id",
  agentUpdateRequest: { name: "NewAgentName" }
});

// Assigning a task
browserUseApi.assignTask({
  agentId: "agent-id",
  taskCreateRequest: { inputData: { url: "https://example.com" } }
});

// Updating a task
browserUseApi.updateTask({
  agentId: "agent-id",
  id: "task-id",
  taskUpdateRequest: { inputData: { url: "https://new-example.com" } }
});
```