---
title: "More control over your AI agent workspace"
date: 2026-07-28
updated: 2026-07-28
description: "Gobii adds workspace search, multiple schedules, native webhooks, and 8 optional pets so persistent AI agents are easier to find, schedule, and trigger."
author: "Will Bonde"
author_url: "/team/"
author_job_title: "Growth & Engineering"
type: news
seo_title: "AI Agent Workspace Search, Schedules, and Webhooks"
seo_description: "Gobii adds workspace search, multiple schedules, native webhooks, and 8 optional pets so persistent AI agents are easier to find, schedule, and trigger."
canonical: "https://gobii.ai/blog/newsletter-2026-07-28-more-control-over-your-agent-workspace/"
slug: "newsletter-2026-07-28-more-control-over-your-agent-workspace"
image: "/static/images/blog/newsletters/newsletter-2026-07-28-agent-workspace-hero.webp"
image_alt: "An organized AI agent workspace with message search, calendar schedules, connected webhooks, and a purple digital pet"
og_image_alt: "An organized AI agent workspace with message search, calendar schedules, connected webhooks, and a purple digital pet"
faq:
  - question: "Can I search messages from every Gobii agent?"
    answer: >-
      Yes. Search spans every conversation. Filter by agent, image, or file, then open the matching message.
  - question: "Can one Gobii agent have multiple schedules?"
    answer: >-
      Yes. A single agent can keep named recurring or one-time schedules, each with its own instructions, timing, timezone, and enabled state.
  - question: "What is the difference between inbound and outbound webhooks?"
    answer: >-
      Inbound webhooks deliver outside events to Gobii. Outbound webhooks send structured results to an approved HTTP endpoint.
  - question: "Can I turn Gobii Pets off?"
    answer: >-
      Yes. Right-click the companion to switch pets or disable them.
tags:
  - newsletter
  - weekly
  - product-updates
  - ai-agent-workspace
  - webhooks
  - agent-schedules
---

<img src="/static/images/blog/newsletters/newsletter-2026-07-28-agent-workspace-hero.webp" alt="An organized AI agent workspace with message search, calendar schedules, connected webhooks, and a purple digital pet" style="max-width: 100%; border-radius: 10px;">

Persistent AI assistants should become easier to direct as their histories grow, not harder. This Gobii release improves the operating layer around long-running activity: retrieve an old result, assign several routines to one assistant, connect outside events, and add a little personality.

Gobii now provides message search across the workspace, several schedules per agent, native inbound and outbound webhook management, and optional Gobii Pets. Every update is available in the product today.

> **Key Takeaways**
>
> - Find prior messages by agent or attachment, then open the exact result.
> - Keep recurring and one-time schedules together, with separate instructions and timezones.
> - Manage inbound and outbound webhooks inside the relevant conversation.
> - More than 10% of Codex users manage three or more agents concurrently during a typical week ([OpenAI, 2026](https://cdn.openai.com/pdf/5d1e1489-21c0-43e4-9d42-f87efdbf0082/the-shift-to-agentic-ai-evidence-from-codex.pdf)).

## What Changed in the Gobii Workspace?

The release adds control at three moments in a long-running workflow. Search retrieves completed output. Multiple schedules set future timing. Native webhooks carry signals between Gobii and other systems. Pets serve a different purpose, offering a small personal touch in an interface people may visit every day.

These capabilities build on the persistent model behind [agent memory](/blog/newsletter-2026-02-24-most-ai-agents-forget-yours-doesn-t/) and [visible agent work](/blog/newsletter-2026-03-10-your-agents-can-show-their-work-now/). Retained context turns history into something useful. New controls let you retrieve that history, set its cadence, and trigger the next action.

<!-- [UNIQUE INSIGHT] -->

Persistence creates value and operational weight at the same time. As conversations, routines, and integrations accumulate, people need stronger retrieval, clearer timing, and visible event paths without changing how they talk to their agents.

## Workspace Search Finds the Exact Message

In a 2026 workplace survey, 56% of employees said they verify AI output, spending an average of two hours each week on that review ([TeamViewer, 2026](https://www.teamviewer.com/en/global/company/press/2026/ai-workplace-autonomy-global-research/)). That review starts with retrieval. Search removes the friction of locating an earlier result, source, file, or instruction before you inspect it.

Press `Cmd+F` on macOS or `Ctrl+F` on Windows. Results cover all Gobii conversations, not only the one on screen. Narrow the query with `agent:`, use `has:image` or `has:file` for attachments, then select a match to jump back to the original message.

<figure style="margin: 2rem 0;">
  <img src="/static/images/blog/newsletters/newsletter-2026-07-28-message-search.webp" alt="Gobii workspace message search showing agent and attachment filters" width="680" height="366" loading="lazy" decoding="async" style="max-width: 100%; height: auto; border-radius: 10px;">
  <figcaption style="margin-top: 0.5rem; font-size: 0.9rem; color: #475569;">Workspace search can narrow results by agent and by messages containing images or files.</figcaption>
</figure>

Old context is useful only when you can recover it. After weeks of activity, you may remember a conclusion but not the exchange that produced it, and search turns the timeline into a usable record instead of an archive that demands manual scanning.

## Multiple Schedules Give One Agent Several Routines

OpenAI reports that more than 10% of Codex users manage at least three concurrent agents at some point in a typical week ([OpenAI, 2026](https://cdn.openai.com/pdf/5d1e1489-21c0-43e4-9d42-f87efdbf0082/the-shift-to-agentic-ai-evidence-from-codex.pdf)). The study covers Codex, not Gobii, yet it highlights a broader need: parallel activity requires explicit timing controls.

A Gobii agent can now keep several named schedules, each with its own instruction, timing, timezone, and enabled state. Set a recurring cadence for a weekday account scan, for example, while adding a one-time follow-up without creating another agent or replacing the existing routine.

<figure style="margin: 2rem 0;">
  <img src="/static/images/blog/newsletters/newsletter-2026-07-28-multiple-schedules.webp" alt="Gobii agent schedule settings with several named schedules" width="1200" height="675" loading="lazy" decoding="async" style="max-width: 100%; height: auto; border-radius: 10px;">
  <figcaption style="margin-top: 0.5rem; font-size: 0.9rem; color: #475569;">Separate schedules let one agent own several recurring and one-time routines.</figcaption>
</figure>

A research agent might collect competitor updates every Monday, prepare a monthly summary on the first business day, and run a one-time check before a planning meeting. One charter and history stay intact while every routine remains independently understandable and controllable.

## Native Webhooks Connect Events in Both Directions

Webhooks handle two directions. An inbound connection lets another system send an event to an existing Gobii agent, while an outbound connection posts structured data to an HTTP endpoint during execution. You can create and manage both inside the conversation, without wiring up a separate automation layer elsewhere.

<figure style="margin: 2rem 0;">
  <img src="/static/images/blog/newsletters/newsletter-2026-07-28-native-webhooks.webp" alt="A Gobii conversation configuring inbound and outbound webhooks" width="1200" height="675" loading="lazy" decoding="async" style="max-width: 100%; height: auto; border-radius: 10px;">
  <figcaption style="margin-top: 0.5rem; font-size: 0.9rem; color: #475569;">Inbound webhooks bring events to an agent; outbound webhooks let the agent send data to another service.</figcaption>
</figure>

A CRM stage change can wake an agent through an inbound webhook. After researching the account and applying standing instructions, it can send a structured result to another approved service through the outbound route. Both the original event and subsequent activity remain in the timeline, making the path easier to inspect.

TeamViewer found that 70% of surveyed employees were comfortable with more AI autonomy when they could step in, while 33% specifically wanted visible activity logs ([TeamViewer, 2026](https://www.teamviewer.com/en/global/company/press/2026/ai-workplace-autonomy-global-research/)). Webhooks alone do not create oversight. A persistent timeline, clear instructions, and approval boundaries make event-driven activity easier to review.

For implementation details, see [Inbound Webhooks](https://docs.gobii.ai/using-gobii/inbound-webhooks), the [Agent API guide](https://docs.gobii.ai/developers/developer-agents), and our earlier guide to [reactive agents with inbound webhooks](/blog/newsletter-2026-04-08-inbound-webhooks/), which covers payload design, secret handling, and safe first workflows.

<!-- [PERSONAL EXPERIENCE] -->

When we build event paths in Gobii, we preserve the incoming event in the timeline while placing lasting behavior in the agent's instructions. Operators can then inspect two distinct records: what happened, and how the response was defined. Native management keeps setup close to both.

## Gobii Pets Add an Optional Bit of Personality

Gobii Pets are small companions that wander around the interface, with eight currently available: Gobii, Eevee, Grizzly, Smudge, Maggie, Clementine, Riby, and Chewie. They neither change agent behavior nor consume a schedule. Their only job is to make the screen feel a little more alive.

<figure style="margin: 2rem 0;">
  <img src="/static/images/blog/newsletters/newsletter-2026-07-28-gobii-pets.webp" alt="The Gobii Pets chooser showing eight optional workspace companions" width="680" height="356" loading="lazy" decoding="async" style="max-width: 100%; height: auto; border-radius: 10px;">
  <figcaption style="margin-top: 0.5rem; font-size: 0.9rem; color: #475569;">Choose one of eight companions, switch pets at any time, or disable the feature.</figcaption>
</figure>

Right-click your pet to choose another companion or turn the feature off. That opt-out is deliberate because a playful detail should remain playful, especially beside focused tasks that may run for a long time.

## Better Controls Make Persistent AI Work Easier to Run

OpenAI's analysis found that active Codex users grew more than fivefold during the first half of 2026. Over the same period, the share assigning tasks estimated to require more than eight human hours rose roughly tenfold ([OpenAI, 2026](https://cdn.openai.com/pdf/5d1e1489-21c0-43e4-9d42-f87efdbf0082/the-shift-to-agentic-ai-evidence-from-codex.pdf)). Those are Codex usage patterns, not Gobii benchmarks, but they show why a dependable operating layer matters.

Longer-running assignments bring practical questions. Can you find an earlier decision, keep several cadences understandable, and let outside systems start activity or receive results through an inspectable path? Search, schedules, and webhooks answer those needs at the workspace level.

The product principle is control without ceremony. Search starts from the keyboard, schedules remain attached to their owner, and webhooks live where you already hold the conversation. Pets stay optional. Each capability keeps its controls close at hand.

## Frequently Asked Questions

### Can I search messages from every Gobii agent?

Yes. Press `Cmd+F` on macOS or `Ctrl+F` on Windows, then filter across conversations by agent, image, or file.

### Can one Gobii agent have multiple schedules?

Yes. Each named schedule has separate instructions, timing, timezone, and enabled state, whether recurring or one-time.

### What is the difference between inbound and outbound webhooks?

Inbound webhooks receive outside events. Outbound webhooks deliver structured results to approved HTTP endpoints.

### Can I turn Gobii Pets off?

Yes. Right-click the pet to switch companions or disable the visual feature. It does not affect behavior, tools, schedules, or usage.

## Sources

- [The Shift to Agentic AI: Evidence from Codex](https://cdn.openai.com/pdf/5d1e1489-21c0-43e4-9d42-f87efdbf0082/the-shift-to-agentic-ai-evidence-from-codex.pdf), OpenAI. Retrieved July 28, 2026.
- [75% of employees use AI daily, but 61% want human oversight before autonomy](https://www.teamviewer.com/en/global/company/press/2026/ai-workplace-autonomy-global-research/), TeamViewer. Retrieved July 28, 2026.
- [Inbound Webhooks](https://docs.gobii.ai/using-gobii/inbound-webhooks), Gobii product docs. Retrieved July 28, 2026.
- [Developer Agents and Agent API](https://docs.gobii.ai/developers/developer-agents), Gobii developer docs. Retrieved July 28, 2026.

## About the Author

Will Bonde works across Growth & Engineering at Gobii, where he helps shape the platform and how its browser-native agents serve customers. [Meet Will and the rest of the Gobii team](/team/).

[Open your Gobii workspace](https://gobii.ai/app/agents?utm_source=blog&utm_medium=web&utm_campaign=20260728&utm_content=cta)
