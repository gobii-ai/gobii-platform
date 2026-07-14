---
title: Why Gobii API?
order: 5
icon: guide
---

**Gobii** is a platform for deploying and managing always-on AI employees in the cloud. The Agent API lets teams create persistent agents, assign charters and schedules, connect tools, exchange messages, inspect activity, and manage each employee's lifecycle programmatically.

## Why use the Gobii API?

- **Persistent agent lifecycle**: Create an employee once, then retrieve, update, activate, deactivate, or delete it through stable REST resources
- **Schedules and events**: Wake employees on recurring schedules, messages, and webhooks without keeping a request open
- **Tools and browser execution**: Connect approved tools and let employees use persistent browser sessions when work lives on the web
- **Observable work**: Inspect timelines, processing status, messages, and recent browser tasks from your application
- **Flexible deployment**: Use Gobii Cloud or self-host the open-source platform in your own infrastructure

## How It Works

1. **Create an employee**: Send a name, charter, and optional schedule to the Agent API
2. **Connect its tools**: Give the employee the integrations and MCP servers required for its responsibility
3. **Start work**: Let a schedule or event wake the employee, or send it a message through the API
4. **Inspect activity**: Read its timeline and processing state, and retrieve recent browser tasks when relevant
5. **Manage the lifecycle**: Update the charter or schedule, pause the employee, reactivate it, or delete it when the responsibility ends

## Common Use Cases

- **Research and monitoring**: Track markets, competitors, candidates, accounts, or public sources on a schedule
- **Sales and recruiting operations**: Keep prospect and candidate research current and prepare structured handoffs for review
- **Event-driven workflows**: Respond to product events, webhooks, email, or SMS with persistent context
- **Embedded agent products**: Create and manage dedicated AI employees behind your own application interface
- **Cross-tool automation**: Carry multistep work across connected apps, files, structured data, and browser sessions

## Getting Started

To begin using Gobii, you'll need to:

1. [Sign up for an account](/accounts/signup/)
2. Get your API key from the Console
3. Send `X-Api-Key: YOUR_API_KEY` with requests to `https://gobii.ai/api/v1`
4. Create your first employee with `POST /agents/`, including its `name`, `charter`, and optional `schedule`

For the current resource fields and lifecycle actions, read the [Agent API documentation](https://docs.gobii.ai/developers/developer-agents).

## Need Help?

If you have any questions or need assistance, don't hesitate to:

- Join our [Discord community](https://discord.gg/yyDB8GwxtE) for support and discussions
- Explore our <a href="/api/schema/swagger-ui/" target="_blank">API Reference</a>
