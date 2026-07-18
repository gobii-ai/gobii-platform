---
title: "Remote MCP Server for Persistent AI Agents"
date: 2026-05-19
updated: 2026-07-18
description: "Gobii Remote MCP exposes 15 tools for creating, messaging, coordinating, debugging, and attaching files to persistent AI agents from compatible clients."
author: "Matt Greathouse"
author_type: "Person"
author_url: "/team/"
author_job_title: "Engineering"
author_bio: "Matt Greathouse is a full-stack engineer at Gobii focused on the secure, reliable infrastructure behind persistent browser-native AI agents."
author_same_as:
  - "https://www.linkedin.com/in/matt-greathouse/"
  - "https://github.com/matt-greathouse"
seo_title: "Remote MCP Server for Persistent AI Agents | Gobii"
seo_description: "Gobii Remote MCP exposes 15 tools for creating, messaging, coordinating, debugging, and attaching files to persistent AI agents from compatible clients."
canonical: "https://gobii.ai/blog/newsletter-2026-05-19-remote-mcp/"
slug: "newsletter-2026-05-19-remote-mcp"
image: "/static/images/blog/newsletters/newsletter-2026-05-19-remote-mcp-hero.webp"
image_alt: "A remote MCP client securely managing persistent Gobii AI agents through an authenticated endpoint"
og_image_alt: "A remote MCP client securely managing persistent Gobii AI agents through an authenticated endpoint"
image_width: 1200
image_height: 630
schema_graph: true
keywords:
  - remote MCP server
  - MCP server for AI agents
  - Model Context Protocol
  - persistent AI agents
  - Streamable HTTP MCP
  - Agent API
tags:
  - newsletter
  - product-updates
  - remote-mcp
  - model-context-protocol
  - AI-agents
  - developer-tools
---

<img src="/static/images/blog/newsletters/newsletter-2026-05-19-remote-mcp-hero.webp" alt="A remote MCP client securely managing persistent Gobii AI agents through an authenticated endpoint" width="1200" height="630" loading="eager" decoding="async" fetchpriority="high" style="border-radius: 10px;">

Persistent AI agents are most useful inside the tools where work already happens. Remote MCP provides that bridge. An external AI client can discover available operations and direct long-lived workers without replacing their timelines, files, schedules, or standing instructions.

This is a different flow from adding an MCP server to a Gobii. Connected MCP servers give a Gobii access to outside tools. Remote MCP turns Gobii into the server, so Claude Code, Codex, Hermes, or an internal MCP client can manage and message Gobii agents.

> **Key takeaways**
>
> - Remote MCP currently exposes 15 tools.
> - Traffic runs from an outside AI client into Gobii, the reverse of a Gobii consuming a connected MCP server.
> - Protect the scoped API key for lifecycle, coordination, messaging, timelines, debugging, and files in an environment variable or managed secret store.

## What Is a Remote MCP Server?

**Remote MCP servers** are network endpoints that expose tools to compatible clients. As of July 2026, Gobii's endpoint provides 15 tools for operating persistent AI agents through one authenticated connection ([Gobii, Remote MCP](https://docs.gobii.ai/developers/mcp-server), retrieved July 18, 2026).

The [Model Context Protocol](https://modelcontextprotocol.io/specification/2025-11-25) standardizes client-server exchanges. It covers tools, resources, prompts, and related capabilities. This implementation focuses on tools: callers list the operations their credentials permit, then invoke them through JSON-RPC messages. Persistence is the anchor. An agent's charter, schedule, apps, files, peer links, and history all remain in Gobii. Rather than copying a worker into the caller, the endpoint provides a controlled route to it.

## How Does Gobii Remote MCP Work?

At `https://gobii.ai/api/v1/mcp/`, Gobii implements the MCP `2025-11-25` JSON-RPC flow over Streamable HTTP. Each client message is a new HTTP `POST`; requests can return JSON, while notifications receive `202 Accepted` ([Gobii, Remote MCP endpoint](https://docs.gobii.ai/developers/mcp-server), retrieved July 18, 2026).

Four steps define the exchange:

1. Connect to the endpoint with an API key.
2. Initialize MCP, then call `tools/list` to discover the operations allowed by that credential.
3. Invoke the selected tool, perhaps `gobii_list_agents`, `gobii_send_agent_message`, or `gobii_get_agent_timeline`, with its documented arguments.
4. Before returning a result, Gobii applies ownership, detailed input validation, lifecycle constraints, credit policy, and permission rules to the requested action.

This isn't a second task system. Timeline reads use durable cursors, and the wait tool can poll for matching events after a known position. For example, a coding agent can delegate research, continue working locally, then return when the Gobii reports fresh activity.

## What Can an MCP Client Do With Gobii Agents?

Today, the Remote MCP server exposes 15 tools in four practical groups: six for agent lifecycle and configuration, three for peer links, four for messaging and visibility, and two for files ([Gobii, Remote MCP tools](https://docs.gobii.ai/developers/mcp-server), retrieved July 18, 2026).

<table>
  <thead>
    <tr><th>Capability</th><th>Remote MCP tools</th><th>Example use</th></tr>
  </thead>
  <tbody>
    <tr><td>Agent lifecycle</td><td><code>gobii_list_agents</code>, <code>gobii_get_agent</code>, <code>gobii_create_agent</code>, <code>gobii_update_agent</code>, <code>gobii_archive_agent</code>, <code>gobii_get_agent_config_options</code></td><td>Create an operations agent, inspect supported configuration, then set its schedule and credit policy.</td></tr>
    <tr><td>Peer coordination</td><td><code>gobii_list_agent_links</code>, <code>gobii_link_agents</code>, <code>gobii_unlink_agents</code></td><td>Link a research agent to a reporting agent while preserving each worker's timeline.</td></tr>
    <tr><td>Messaging and visibility</td><td><code>gobii_send_agent_message</code>, <code>gobii_get_agent_timeline</code>, <code>gobii_get_agent_debug_trace</code>, <code>gobii_wait_for_agent_event</code></td><td>Send a brief, wait for a matching event, then inspect the result or sanitized debug context.</td></tr>
    <tr><td>Files</td><td><code>gobii_list_agent_files</code>, <code>gobii_upload_agent_file</code></td><td>Upload a brief, attach it to a message, and list the agent's filespace afterward.</td></tr>
  </tbody>
</table>

<!-- [PERSONAL EXPERIENCE] -->

During implementation, preserving the timeline mattered more than inventing another run abstraction. Outside callers see discrete tool invocations; the Gobii retains one continuous work history across chat, schedules, files, and peer messages.

## Remote MCP and Connected MCP Servers Use Opposite Flows

Official Gobii documentation names two separate MCP concepts. Connected MCP servers let a Gobii call outside tools; Gobii Remote MCP lets an outside AI client call Gobii tools ([Gobii, MCP Servers](https://docs.gobii.ai/using-gobii/mcp-servers), retrieved July 18, 2026). Their capabilities and trust boundaries differ because traffic moves in opposite directions.

<img src="/static/images/blog/newsletters/newsletter-2026-05-19-remote-mcp-flow.svg" alt="Diagram comparing Remote MCP flowing from an external AI client into Gobii with connected MCP servers flowing from a Gobii to external tools" width="1200" height="675" loading="lazy" decoding="async" style="border-radius: 10px;">

<table>
  <thead>
    <tr><th>Direction</th><th>Gobii's role</th><th>What becomes available</th></tr>
  </thead>
  <tbody>
    <tr><td>External client → Gobii Remote MCP</td><td>MCP server</td><td>Agent management, messaging, timeline, coordination, debug, and file tools become available to the client.</td></tr>
    <tr><td>Gobii → connected MCP server</td><td>MCP client</td><td>The connected server's external tools and data become available to an assigned Gobii.</td></tr>
  </tbody>
</table>

<!-- [UNIQUE INSIGHT] -->

Direction is the fastest way to choose. If Claude Code needs to operate a Gobii, use Remote MCP. When a Gobii needs an internal database or outside service, connect that service's MCP server instead; our earlier [MCP support announcement](/blog/newsletter-2025-11-11-agents-just-got-way-more-connected-mcp-support-is-here/) explains that latter flow in detail.

## Remote MCP vs Agent API, Webhooks, and App Integrations

Four developer-facing integration surfaces start work differently. Remote MCP serves an AI client, the Agent API serves application code, webhooks deliver events, and connected apps give an agent provider-specific actions ([Gobii, Build With Gobii](https://docs.gobii.ai/start-here/build-with-gobii), retrieved July 18, 2026).

<table>
  <thead>
    <tr><th>You need to...</th><th>Use</th><th>Why</th></tr>
  </thead>
  <tbody>
    <tr><td>Let Claude Code, Codex, Hermes, or another MCP client operate Gobii</td><td><strong>Remote MCP</strong></td><td>The outside AI client discovers and calls Gobii's tools.</td></tr>
    <tr><td>Build a deterministic application around agent resources</td><td><strong>Agent API</strong></td><td>REST endpoints create, update, schedule, activate, message, and inspect persistent agents.</td></tr>
    <tr><td>Wake an existing agent when another system emits an event</td><td><strong>Inbound webhook</strong></td><td>The source sends fresh event data into one agent's timeline.</td></tr>
    <tr><td>Let a Gobii work inside a SaaS product</td><td><strong>Connected app</strong></td><td>The provider becomes an agent tool through its supported authentication and permission model.</td></tr>
    <tr><td>Give a Gobii tools from another MCP service</td><td><strong>Connected MCP server</strong></td><td>Gobii consumes that server's external tools as the client.</td></tr>
  </tbody>
</table>

Remote MCP and the [Agent API](https://docs.gobii.ai/developers/developer-agents) overlap in lifecycle operations, yet they serve different callers. Choose REST for deterministic workflows. Choose MCP when an AI client should inspect the available tools and select an appropriate operation from the current instruction. In production, these surfaces can coexist with clear responsibilities; see [inbound webhooks for reactive agents](/blog/newsletter-2026-04-08-inbound-webhooks/) and [one-click AI agent integrations](/blog/newsletter-2026-03-17-one-click-integrations-for-your-agents/) for the other directions.

## How Do You Connect an MCP Client?

Setup needs an endpoint, Streamable HTTP, and an authentication header. Choose either `X-Api-Key` or `Authorization: Bearer`; whichever format you use, the key must accompany every request to the production service ([Gobii, Remote MCP authentication](https://docs.gobii.ai/developers/mcp-server), retrieved July 18, 2026).

For Hermes, keep the key in the profile environment and reference it from `config.yaml`:

```yaml
mcp_servers:
  gobii:
    url: "https://gobii.ai/api/v1/mcp/"
    headers:
      Authorization: "Bearer ${GOBII_API_KEY}"
    timeout: 60
    connect_timeout: 10
    tools:
      include:
        - gobii_list_agents
        - gobii_get_agent
        - gobii_send_agent_message
        - gobii_get_agent_timeline
        - gobii_wait_for_agent_event
```

That allowlist is intentionally narrow. Add lifecycle, peer-link, file, or debug operations only when needed. Hermes supports a remote `url`, request `headers`, timeouts, and include or exclude policies ([Hermes Agent, MCP Config Reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference), retrieved July 18, 2026). Configuration syntax varies. Claude Code supports remote HTTP servers and custom authentication headers ([Anthropic, Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp), retrieved July 18, 2026). Follow the client's current instructions while keeping the endpoint and credential rules unchanged.

Test with a read-only sequence first:

1. List agents in the owner scope.
2. Retrieve one known worker and confirm its identity.
3. Read its timeline.
4. Only then, send a narrowly framed message after every prior check returns the intended worker and expected history.
5. Preserve the returned cursor before waiting for new activity.

## How Should You Protect API Keys and Permissions?

Both supported authentication headers preserve the underlying scope. Personal keys expose agents accessible to that user; organization keys expose organization-owned workers, and every tool call inherits the supplied credential's permissions ([Gobii, Remote MCP authentication](https://docs.gobii.ai/developers/mcp-server), retrieved July 18, 2026).

<!-- [PERSONAL EXPERIENCE] -->

Store the key in an environment variable or managed secret. Never paste it into chat, commit it, or place it in a query string. After exposure, rotate. In our experience, a small allowlist clarifies the initial connection. Start with list, get, timeline, and message operations; add creation, updates, files, peer links, or debug access only as the workflow expands and each permission earns a purpose. Browser `Origin` validation also reduces DNS rebinding risk. This behavior follows the official Streamable HTTP guidance, which requires origin validation and recommends authentication for every connection ([Model Context Protocol, Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), retrieved July 18, 2026). Remote access controls who can call Gobii, while sandboxing limits what the runtime can reach afterward; [our production sandboxing guide](/blog/how-we-sandbox-ai-agents-in-production/) explains that second boundary.

## What Are the Current Transport Limitations?

Remote MCP v1 has three deliberate boundaries: it issues no `Mcp-Session-Id`, exposes no standalone GET SSE stream, and does not create an MCP task or run abstraction. It maps MCP calls onto Gobii's existing agent timeline instead ([Gobii, Remote MCP limitations](https://docs.gobii.ai/developers/mcp-server), retrieved July 18, 2026).

On the wire, this is Streamable HTTP. Send each JSON-RPC message as a new `POST` whose `Accept` header permits both `application/json` and `text/event-stream`, matching the negotiated content types expected by compatible clients. Requests return JSON; notifications or responses receive `202 Accepted`. Version 1 advertises tools only. File operations use agent filespaces; before referencing especially large assets from a message, place them there through a suitable transfer path outside MCP. Arbitrary URL fetching is excluded. These boundaries narrow troubleshooting considerably. `GET /api/v1/mcp/` returns the expected `405 Method Not Allowed`. Use MCP initialization or the documented `POST` smoke flow for a meaningful check; clients that probe standalone SSE should be reconfigured for Streamable HTTP before diagnosis continues.

## Frequently Asked Questions

These five answers cover the distinctions and v1 constraints that most often affect setup. They reflect the 15-tool Remote MCP surface and the separate connected-server flow documented by Gobii as of July 18, 2026 ([Gobii, Remote MCP](https://docs.gobii.ai/developers/mcp-server), retrieved July 18, 2026).

### Is Gobii Remote MCP the same as connecting an MCP server to a Gobii?

No. Traffic moves in reverse. Connected MCP servers give a Gobii tools from an outside service. With Remote MCP, Gobii becomes the server so an external AI client can create, manage, message, coordinate, and inspect persistent agents.

### Does Gobii Remote MCP create separate tasks or runs?

No. Remote MCP preserves the agent's context. Messages append there. Gobii creates no separate MCP task, run, conversation, or session abstraction that divides the timeline, history, files, or ongoing work.

### Which MCP clients can connect to Gobii?

Use a client that supports remote MCP servers over Streamable HTTP and can send an authentication header. Compatible choices include Claude Code, Codex, Hermes, and custom internal clients. Their configuration syntax and security controls differ.

### Can personal and organization API keys use Remote MCP?

Yes. Personal keys expose agents available to that user. Organization keys scope tools to organization-owned agents. Every tool call runs with the permissions of the supplied key, so choose the narrowest owner scope that fits the workflow.

### Does Remote MCP v1 expose resources, prompts, or a standalone SSE stream?

No. Version 1 exposes MCP tools rather than resources or prompts. It returns JSON for requests and accepts notifications with 202 responses, but it does not open a standalone GET SSE stream or issue MCP session IDs.

## Start With a Narrow Remote MCP Connection

Its documented 15-tool catalog supports a staged rollout: begin with agent discovery, retrieval, timeline reading, messaging, and event waiting ([Gobii, Remote MCP](https://docs.gobii.ai/developers/mcp-server?utm_source=blog&utm_medium=web&utm_campaign=20260519&utm_content=cta), retrieved July 18, 2026). Verify ownership and observe a complete exchange before widening the allowlist to mutation, coordination, debug, or file operations. That sequence creates an auditable, reversible baseline.
