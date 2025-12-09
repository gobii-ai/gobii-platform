---
title: "How to Get 20–60x More Work Per Dollar — Securely — With Gobii and DeepSeek V3.2"
date: 2025-12-09
description: "What changes when intelligence becomes a commodity—and how to build on it without betting the company"
author: "Andrew I. Christianson"
seo_title: "How to Get 20–60x More Work Per Dollar — Securely — With Gobii and DeepSeek V3.2"
seo_description: "How Gobii turns DeepSeek V3.2 into real work at 20-60x lower cost, while keeping your data secure and your workflows portable."
tags:
  - deepseek
  - open source
  - ai agents
  - deepseek v3.2
  - open weights
  - garak
  - llm security
---

Most AI discussion this year has circled the same question:

> "Which model is the best?"

Anyone running a budget eventually adds a second one:

> "How cheaply can we turn this into real work?"

DeepSeek V3.2 is the first time in a while the leaderboard and the spreadsheet point at the same model.

This post is how Gobii thinks about DeepSeek, open weights, and why so many teams now run their DeepSeek workloads *through* Gobii instead of pointing raw APIs at their problems.

---

## 1. DeepSeek and the Unit Price of "Smart"

DeepSeek is a comparatively small lab in Hangzhou funded by the High-Flyer hedge fund. In under two years it went from an unfamiliar name to being mentioned alongside OpenAI and Anthropic by focusing on one thing: cost.

DeepSeek V3.2's public API prices are roughly:

- **$0.028 per million input tokens (cache hit)**
- **$0.28 per million input tokens (cache miss)**
- **$0.42 per million output tokens**

Compare that to common Western frontier models:

| Model              | Input / M | Output / M |
|--------------------|-----------|------------|
| **DeepSeek V3.2**  | $0.28     | $0.42      |
| GPT-5.1            | $1.25     | $10.00     |
| Claude Sonnet 4.5  | $3.00     | $15.00     |
| Claude Opus 4.5    | $5.00     | $25.00     |

(Prices from OpenAI and Anthropic's public pricing pages, as of late 2025.)

In real workloads, output tokens usually dominate the bill. Being ~24–60× cheaper on output isn't "nice optimization." It moves entire projects from *"we can't justify this"* to *"we'd be foolish not to."*

Then there's the licensing side:

- DeepSeek's R1 reasoning models ship as **open weights under the MIT license**
- You can download, modify, self-host, and distill from them
- V3-series models continue that open-weights pattern

Cheap API. Open weights. Strong reasoning and coding performance, including competitive results against GPT-class models on math, code, and reasoning benchmarks.

<figure>
  <img src="/static/images/blog/deepseek-v3.2-benchmarks.jpg" alt="DeepSeek V3.2 benchmark comparison showing competitive performance against GPT-5-High, Claude-4.5-Sonnet, and Gemini-3.0-Pro across reasoning and agentic tasks" style="max-width: 100%; border-radius: 8px;">
  <figcaption style="font-size: 0.85em; color: #666; margin-top: 0.5em; text-align: center;">DeepSeek V3.2 holds its own against frontier models on reasoning and agentic benchmarks—at a fraction of the cost.</figcaption>
</figure>

Markets noticed. When DeepSeek's reasoning stack broke out in early 2025, Nvidia shed on the order of ~$600B in market cap in a single day—the largest one-day loss in U.S. history—as investors tried to reprice what a 20–50× cost shift in "intelligence" might mean for AI infra.

You don't have to believe every headline to accept the core fact: **intelligence, as an input, just got radically cheaper.**

---

## 2. Cheap Tokens Are Input, Not Outcome

No customer buys tokens. They buy outcomes.

What actually matters is:

- **Leads per dollar**
- **Tickets closed per dollar**
- **Hours saved per dollar**
- **Risk reduced per dollar**

Model quality and per-token pricing are upstream. The real metric is something closer to: *"How much real work do we get for each dollar we spend on AI?"*

A lot of "agent" products stop too early:

- A chat UI with some buttons is not a workforce
- A brittle chain of prompts is not an operation
- A pretty log viewer is not accountability

We're in a world where multiple models are "good enough" on raw IQ. The new bottleneck is whether you can:

1. Point that intelligence at **browsers and SaaS tools**, not just JSON.
2. Run it **continuously**, not just when someone remembers to click "Run."
3. Swap brains **without** tearing your stack apart.

DeepSeek V3.2 gives you an underpriced brain and the option to self-host it.

It does **not** give you:

- Workers with schedules
- A browser automation layer
- Guardrails, logging, and routing
- A way to move between models without re-wiring everything

That's the gap Gobii exists to fill.

---

## 3. What Gobii Adds on Top of DeepSeek

Gobii is an open-core AI workforce platform. The job is simple:

> Turn models—especially cheap, strong ones like DeepSeek V3.2—into **reliable browser workers** that do real jobs.

Concretely, Gobii gives you:

### Browser-native workers

Agents that:

- Log into your tools
- Navigate web apps
- Fill and submit forms
- Scrape results
- Write back into CRMs, ATSs, spreadsheets, ticketing systems, and more

They operate the same interfaces your human team does, inside hardened browser sessions.

### Always-on behavior

Workers that:

- Run on schedules
- React to triggers (new leads, new tickets, new signups)
- Maintain state and memory
- Chain into other workers

In practice, they behave more like a small, configurable team than a one-off chat window.

### Per-worker model policies

Each worker has its own model policy, for example:

- Default to **DeepSeek V3.2** for high-volume, cost-sensitive, low-risk tasks
- Escalate to **GPT-5.1** or **Claude 4.5** when their strengths justify the cost
- Pin to **self-hosted models** for sovereign or regulated workloads

You change the policy, not the worker's job description.

### Open-core, self-hostable architecture

Gobii can run:

- On Gobii's managed cloud
- In your VPC
- In fully air-gapped environments

The core stack is open-core and inspectable. If you need to harden, fork, or extend it for your environment, you can.

### What usage data is telling us

When DeepSeek V3.2 went live on OpenRouter, Gobii integrated it immediately.

Within days, OpenRouter's own stats showed **Gobii as the #1 application by usage for DeepSeek V3.2**, by tokens, across apps that opted into tracking.

You could treat that as a vanity metric. We treat it as revealed preference:

- Users had access to DeepSeek V3.2 directly, via API, and through many other apps
- A disproportionate share of real workloads ended up routed through Gobii instead

If your goal is "maximum work per DeepSeek token," the pattern is clear: **teams keep choosing to run DeepSeek through Gobii workers.**

<figure>
  <img src="/static/images/blog/gobii-openrouter-deepseek-v3.2-leaderboard.png" alt="OpenRouter Apps leaderboard showing Gobii at #1 for DeepSeek V3.2 usage with 18.9B tokens" style="max-width: 100%; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
  <figcaption style="font-size: 0.85em; color: #666; margin-top: 0.5em; text-align: center;">Gobii leads OpenRouter's DeepSeek V3.2 usage at 18.9B tokens—nearly 3× the next application.</figcaption>
</figure>

---

## 4. Open Source as a Control Surface

Gobii's founder, Andrew I. Christianson, has been building and shipping open-source infrastructure for years: Apache NiFi, RA.Aid, and now Gobii.

Internally, we don't treat open source as a slogan. We treat it as a **control surface**:

- It lets you see what your system is actually doing
- It gives you an exit if a vendor disappears or changes direction
- It lets you move components on-prem or into specific regions when regulators or customers require it

DeepSeek leaned into this at the model layer: open weights, MIT licensing, permissive distillation.

Gobii mirrors it at the workforce layer:

- The core browser/agent stack is open-core and self-hostable
- You can run the same workers on Gobii's cloud, your infrastructure, or both
- If you need to fork or customize for a particular environment, you aren't blocked

Gobii is backed by [Open Core Ventures](https://opencoreventures.com), the firm led by Sid Sijbrandij (founder of GitLab). OCV's portfolio is built around the thesis that open-core infrastructure wins in the long run—and that thesis extends across the AI stack. More on that in a moment.

Put simply:

- **DeepSeek gives you cheap, movable brains**
- **Gobii gives those brains cheap, movable bodies**

You decide where the brains live (DeepSeek cloud, OpenRouter, self-hosted) and where the bodies live (Gobii cloud, your VPC, air-gapped).

---

## 5. The China Question, Treated Like an Engineering Constraint

There's a real-world constraint you can't ignore:

- DeepSeek is headquartered in China
- Its hosted API stores data on servers in China
- It is subject to PRC law around data access and intelligence

That has already led to:

- Bans or blocks in parts of the EU on the DeepSeek app over privacy/transparency concerns
- A full ban on DeepSeek from Australian federal systems and devices
- "Do not use in any capacity" guidance in some U.S. government environments

Independent policy and security groups have also been clear about PRC intelligence law: Chinese firms can be compelled to grant access to data.

So there are really three classes of workload:

1. **Clearly fine for hosted DeepSeek**
   - Public web research
   - Enrichment of already-public data
   - Generic content you'd happily publish

2. **Clearly not fine for any third-party API**
   - Trade secrets
   - Regulated PII
   - Anything whose leak is existential or reportable

3. **Grey-zone workloads**
   - Internal but non-sensitive data
   - Situations where jurisdiction and governance matter, but risk is manageable

Our stance at Gobii:

- Treat DeepSeek as **one model in a portfolio**, not a single point of failure
- Use **hosted DeepSeek V3.2** where the economics are compelling and the data is non-sensitive
- Use **self-hosted DeepSeek / other open-weights models** where you want cost/performance without external data residency
- Use **GPT-class and Claude-class models** for workloads where their behavior, guarantees, or compliance posture earn the premium

### Verifying open-weights models before you trust them

Self-hosting solves the data residency problem—but it introduces a new one: **how do you know a model behaves the way you expect?**

Open weights means you can inspect the architecture. It doesn't mean you've tested every edge case, probed for jailbreaks, or verified the model won't leak training data under adversarial prompts.

This is where [Garak](https://github.com/NVIDIA/garak) comes in. Garak is an open-source LLM vulnerability scanner—think of it as a security audit tool for language models. It probes models for:

- Prompt injection vulnerabilities
- Data leakage and memorization
- Jailbreak susceptibility
- Harmful output generation
- Unexpected behavior under adversarial inputs

Like Gobii, Garak is backed by [Open Core Ventures](https://www.opencoreventures.com/blog/https-for-ai-garak-launches-end-to-end-llm-security-platform). The two projects sit at different layers of the stack but share the same premise: if you're going to run AI in production, you need open, inspectable tooling to verify it's doing what you think it's doing.

The combination looks like this:

- **Garak** verifies that your open-weights model (DeepSeek, Llama, Mistral, etc.) behaves correctly before you deploy it
- **Gobii** turns that verified model into browser workers that do real jobs, with logging, guardrails, and audit trails

If you're self-hosting DeepSeek to avoid the China question, you should also be testing it before you trust it. Garak is how.

This is where Gobii's architecture matters:

- Workers are defined at the workflow level, not at the model level
- You can change the model policy without changing the workflow definition
- You can log, audit, and route based on data class, region, or tenant

No drama, no absolutism. Just: use the cheapest safe thing by default, and move up the cost curve when you must.

---

## 6. Models Are Commodities With Personality. Workflows Are Assets.

The public conversation treats "GPT vs Claude vs DeepSeek" like a winner-take-all contest.

From Gobii's perspective:

- Models are **commodities with different strengths and failure modes**
- Workflows are **the asset you actually own**

In Gobii, a worker is defined by:

- The sites and tools it interacts with
- The steps it takes and tools it calls
- The guardrails and review rules it follows
- The data it's allowed to see and modify

Under the hood, each worker has a **model policy**, for example:

- **Default:**
  DeepSeek V3.2 for high-volume, low-sensitivity tasks (research, enrichment, first-pass outreach, routine operations).

- **Premium:**
  GPT-5.1, Claude Sonnet, or Claude Opus for harder reasoning, nuanced writing, or where their behavior earns their price.

- **Sovereign:**
  Self-hosted DeepSeek or other open-weights models for regulated industries, specific geographies, or customers with strict data requirements.

That policy can change tomorrow without rebuilding the worker.

That's the real leverage:

- DeepSeek and similar models pull the marginal cost of "smart" down by 20–60× for many workloads
- Open weights and open-core design let you move that "smart" wherever it needs to run
- Gobii makes "which model?" an implementation detail of "which worker, doing which job?"

When the next cheap, capable model appears (and it will), you shouldn't have to re-platform. You should:

- Add it to the pool
- Route some traffic
- Compare quality and cost
- Keep it if it wins, roll it back if it doesn't

---

## 7. What Rational Teams Do With This

If AI is just a slide on a pitch deck, any choice will do.

If you're actually trying to ship and scale, the environment now looks like this:

- Intelligence is **cheap and getting cheaper**
- The most aggressive price/performance moves right now are coming from DeepSeek and other open-weights players
- GPT- and Claude-class models remain excellent—but at frontier prices
- Regulators and security teams are tightening expectations around data, jurisdiction, and auditability
- The "best" model on paper is likely to change multiple times before your current roadmap is done

In that world, teams that care about output per dollar tend to converge on the same shape:

- **Default to the cheapest strong model** (today, that's often DeepSeek V3.2) for everything it can safely handle
- **Wrap it in a workforce layer** (Gobii) that lives in the browser, is open-core, and cleanly separates workflows from model choice
- **Escalate** to GPT-, Claude-, or fully self-hosted options where they earn the premium
- Make configuration—not rewrites—the main lever

The usage data lines up with that:

- DeepSeek drives down the cost of "smart"
- Gobii has become the top OpenRouter application for DeepSeek V3.2 by usage
- Teams that want maximum work per token are already acting as if "DeepSeek through Gobii" is the default path

We're comfortable with that implication.

---

## Get started

Two straightforward ways to try this:

- **Run Gobii yourself**
  - Open-core, self-hostable platform
  - GitHub: [https://github.com/gobii-ai/gobii-platform](https://github.com/gobii-ai/gobii-platform)

- **Use Gobii as a managed service**
  - Spin up workers in minutes
  - Dashboard and docs: [https://gobii.ai](https://gobii.ai)

Either way, the architecture is the same: treat intelligence as the cheap part, and put the real design work into the layer that turns it into actual, repeatable work.
