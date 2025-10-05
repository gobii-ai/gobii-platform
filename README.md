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

Gobii is the open-source platform for always-on, web-browsing AI agents. Spin it up with Docker Compose, breeze through a first-run wizard, and you have self-hosted agents that navigate the web, gather structured insight, and keep working long after you log out. Prefer a managed experience? Gobii Cloud at [gobii.ai](https://gobii.ai) delivers the same agent stack as a hosted service.

## What Makes Gobii Different
- **Browser-use superpowers**: Each agent can search, click, fill forms, download files, and return structured JSON on demand.
- **Always-on agents**: Communicate with agents over email, web chat, or API, then let them handle follow-ups without manual checkpoints.
- **Own the runtime, choose the cloud**: MIT-licensed code keeps the core under your control, with Gobii Cloud available when you want SLAs and zero-ops hosting.

## Launch in Minutes
1. **Prerequisites**: Docker with at least 12 GB RAM allocated to its VM and a few GB of disk.
2. **Clone & enter the repo**
   ```bash
   git clone https://github.com/gobii-ai/gobii-platform.git
   cd gobii-platform
   ```
3. **Start Gobii** (first run and whenever dependencies change)
   ```bash
   docker compose up --build
   ```
4. Visit [http://localhost:8000](http://localhost:8000) and follow the first-run wizard:
   - Create the first admin account.
   - Pick the LLM provider (OpenAI, OpenRouter, Anthropic, Fireworks, or custom) powering your agents.
   - Drop in the API keys and preferred models. You can route browser-use calls to a different model if you’d like.
5. After the redirect, sign in at [http://localhost:8000/](http://localhost:8000/) and start building agents.

Need scheduling, inbox listeners, or extra telemetry later? Launch the optional `beat`, `email`, or `obs` profiles with `docker compose --profile <name> up`.

## Workflows You Can Ship Today
- Create agents, attach secrets, and wire up email or web chat handoffs.
- Hand teammates or services an API key so they can trigger browser-use jobs without touching the UI.
- Watch tasks stream results, capture structured JSON, or download files created along the way.

### Try the API
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

## Choose Your Path
| Self-Host (this repo) | Gobii Cloud (managed) |
| --- | --- |
| MIT-licensed core, data stays on your infra. | Zero-ops hosting, governed releases, SLAs. |
| Customize runtime, networking, branding, and integrations. | Autoscaling agents, managed upgrades, enterprise support. |
| Community support via GitHub issues & Discord. | Dedicated success and support (contracted). |

## Contribute & Connect
- Share ideas or bugs in GitHub issues.
- Follow existing style (ruff/black) when submitting PRs.
- Join the community on [Discord](https://discord.gg/yyDB8GwxtE).

## License & Trademarks
- Source code ships under the [MIT License](LICENSE).
- The Gobii name and logo are trademarks of Gobii, Inc. See [NOTICE](NOTICE) for guidance.
- Proprietary mode and non-MIT components require a commercial agreement with Gobii, Inc.

---

Built with ❤️ by the Gobii team. Let us know what you ship with it!
