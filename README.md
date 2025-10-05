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

**The production platform for [browser-use](https://github.com/browser-use/browser-use) agents** 🚀

Gobii is the open-source platform for deploying and managing [browser-use](https://github.com/browser-use/browser-use) agents at scale. While browser-use gives AI agents powerful browser automation capabilities, Gobii provides the infrastructure to run them in production: always-on execution, scheduling, email/API triggers, secret management, and team collaboration. Spin it up with Docker Compose, complete a first-run wizard, and you have self-hosted browser-use agents that work 24/7. Prefer managed hosting? Gobii Cloud at [gobii.ai](https://gobii.ai) delivers the same platform as a service.

## What Makes Gobii Different
- **Production infrastructure for browser-use**: Turn browser-use agents into always-on services with scheduling, email triggers, API endpoints, and persistent execution.
- **Self-hosted or managed**: MIT-licensed platform you can run anywhere, or Gobii Cloud for zero-ops hosting with SLAs.
- **Built for teams**: Share agents, manage secrets, collaborate on workflows, and control access across your organization.

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
   - Pick the LLM provider (OpenAI, OpenRouter, Anthropic, Fireworks, or custom) for your agents.
   - Drop in the API keys and preferred models. You can route browser-use agent calls to a different model if you'd like.
5. After the redirect, sign in at [http://localhost:8000/](http://localhost:8000/) and start deploying browser-use agents.

Need scheduling, inbox listeners, or extra telemetry later? Launch the optional `beat`, `email`, or `obs` profiles with `docker compose --profile <name> up`.

## What You Can Build
- Deploy browser-use agents with persistent execution, secret management, and email/web chat interfaces.
- Expose browser-use capabilities via API endpoints for teammates or external services.
- Monitor agent execution, capture structured outputs, and manage files generated during workflows.

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

## The Platform for browser-use

[browser-use](https://github.com/browser-use/browser-use) is the leading open-source library for AI-powered browser automation, giving agents human-like web interaction capabilities. Gobii provides the production infrastructure to deploy and scale these agents:

**browser-use brings the automation:**
- Advanced web navigation, form filling, and data extraction
- Visual understanding of web pages for smarter interactions
- Robust error handling and retry mechanisms

**Gobii brings the platform:**
- Always-on agent execution and scheduling
- Email triggers, API endpoints, and web interfaces
- Secret management, team collaboration, and access control
- Monitoring, logging, and structured output capture

Want to contribute to the browser automation layer? Check out [browser-use on GitHub](https://github.com/browser-use/browser-use).

## Contribute & Connect
- Share ideas or bugs in GitHub issues.
- Follow existing style (ruff/black) when submitting PRs.
- Join the community on [Discord](https://discord.gg/yyDB8GwxtE).

## License & Trademarks
- Source code ships under the [MIT License](LICENSE).
- The Gobii name and logo are trademarks of Gobii, Inc. See [NOTICE](NOTICE) for guidance.
- Proprietary mode and non-MIT components require a commercial agreement with Gobii, Inc.

---

Built with ❤️ by the Gobii team. The production platform for [browser-use](https://github.com/browser-use/browser-use) agents.
