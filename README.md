<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo/noBgWhite.png" />
    <source media="(prefers-color-scheme: light)" srcset="assets/logo/noBgBlack.png" />
    <img src="assets/logo/noBgBlack.png" alt="Gobii logo" width="160" />
  </picture>
</p>

# Gobii Platform

![License](https://img.shields.io/badge/license-MIT-green.svg)
![Docker Compose](https://img.shields.io/badge/docker-compose-blue?logo=docker)
![Status](https://img.shields.io/badge/status-early%20access-orange)

**Always-on AI employees for teams.**

Gobii is an open-source platform for running durable autonomous agents in production.
Each agent can run continuously, wake from schedules and events, use real browser automation, call external systems, and coordinate with other agents.

If you want local-first, single-user assistant UX, there are great options.
Gobii is built for a different job: secure, cloud-native, always-on agent operations for real business workflows.

<div style="width: 100%; text-align: center">
  <video
    src="https://github.com/user-attachments/assets/b18068c6-695c-4a21-ac08-c298218b7882"
    width="800"
    controls
    muted
    loop
    playsinline
    poster="https://github.com/user-attachments/assets/ab12cd34-ef56-7890-gh12-ijkl3456mnop"
    style="border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,0.15);max-width:100%;height:auto;"
  >
  </video>
  <br/>
  <em>Gobii agent demo in action</em>
</div>

## What Gobii Is

- **Always-on runtime**: per-agent schedule plus event queue continuity, not just isolated turns.
- **Agent identity**: endpoint-addressable agents (email/SMS/web), including managed identities like `first.last@my.gobii.ai`.
- **Native agent-to-agent coordination**: linked agents can message each other directly.
- **Webhook-native integration model**: inbound webhooks wake agents; outbound webhooks are first-class agent actions.
- **Based on browser-use**: Gobii builds on browser-use and keeps `/api/v1/tasks/browser-use/` for browser automation workflows.
- **SQLite-backed memory substrate**: structured, tool-friendly state that persists across runs.
- **Real browser execution**: fully headed browser support, profile persistence, and proxy-aware task routing.
- **Security-first operations**: encrypted secrets, proxy-governed egress, and sandbox compute designed for Kubernetes/gVisor environments.

## Architecture in One View

```text
External events/channels (email, SMS, webhooks, API)
                     │
                     ▼
            Durable per-agent event queue
                     │
                     ▼
         Persistent agent runtime + schedule
                     │
                     ├─ Browser automation (headed/profile-aware)
                     ├─ SQLite state + structured memory
                     ├─ Outbound webhooks + HTTP integrations
                     └─ Native agent-to-agent messaging
                     │
                     ▼
      Replies, actions, files, and downstream integrations
```

## Launch in Minutes

1. Prerequisites: Docker Desktop (or compatible engine) with at least 12 GB RAM allocated to its VM.
2. Clone and enter the repo.

```bash
git clone https://github.com/gobii-ai/gobii-platform.git
cd gobii-platform
```

3. Start Gobii.

```bash
docker compose up --build
```

4. Visit [http://localhost:8000](http://localhost:8000) and complete first-run setup.

- Create your admin account.
- Choose model providers (OpenAI, OpenRouter, Anthropic, Fireworks, or custom endpoint).
- Add keys and preferred models.

5. Sign in and create your first always-on agent.

Optional runtime profiles:

- `docker compose --profile beat up` for scheduled trigger processing.
- `docker compose --profile email up` for IMAP idlers/inbound email workflows.
- `docker compose --profile obs up` for Flower + OTEL collector observability services.

## API Quick Start

```bash
curl --no-buffer \
  -H "X-Api-Key: $GOBII_API_KEY" \
  -H "Content-Type: application/json" \
  -X POST http://localhost:8000/api/v1/tasks/browser-use/ \
  -d '{
        "prompt": "Visit https://news.ycombinator.com and return the top headline",
        "wait": 60,
        "output_schema": {
          "type": "object",
          "properties": {"headline": {"type": "string"}},
          "required": ["headline"],
          "additionalProperties": false
        }
      }'
```

## Core Capabilities

| Capability | Gobii approach |
| --- | --- |
| Always-on behavior | Schedule + event trigger lifecycle with durable processing |
| Event ingress | SMS/email/webhook/API events feeding a unified runtime loop |
| Outbound integration | Agent-invoked webhooks and HTTP actions |
| Memory | SQLite-backed state and tool tables |
| Multi-agent | Native peer agent messaging and coordination |
| Browser runtime | Headed browser support with persistent profile handling |
| Secrets | Encrypted-at-rest secret records integrated into tool execution |
| Egress control | Proxy selection, health-aware routing, dedicated proxy inventory |
| Sandbox posture | Kubernetes-backed sandbox compute with gVisor runtime-class support |

## Choose Your Path

| Self-host (this repo) | Gobii Cloud (managed) |
| --- | --- |
| MIT-licensed core on your own infra. | Managed Gobii deployment and operations. |
| Full control over runtime, networking, and integration behavior. | Governed releases, operational support, and managed scaling. |
| Ideal for teams that want source-level customization. | Ideal for teams that want faster production rollout. |

## Development

Use the local development guide in [DEVELOPMENT.md](DEVELOPMENT.md).

Typical local loop:

- `docker compose -f docker-compose.dev.yaml up` for backing services.
- `uv run uvicorn config.asgi:application --reload --host 0.0.0.0 --port 8000` for Django.
- `uv run celery -A config worker -l info --pool=threads --concurrency=4` for workers.

## Security and Sandbox Notes

The repository includes Kubernetes sandbox compute wiring and design docs for stronger isolation and controlled egress:

- [Sandbox compute spec](docs/design/sandbox_pods_compute_spec.md)
- [Sandbox compute ops notes](docs/design/sandbox-compute-ops.md)

## Contribute and Connect

- Open issues and PRs are welcome.
- Follow existing project formatting and test conventions.
- Join the community on [Discord](https://discord.gg/yyDB8GwxtE).

## License and Trademarks

- Source code is licensed under [MIT](LICENSE).
- Gobii name and logo are trademarks of Gobii, Inc. See [NOTICE](NOTICE).
- Proprietary-mode and non-MIT components require a commercial agreement with Gobii, Inc.
