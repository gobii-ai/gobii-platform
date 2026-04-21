---
title: "Using SQLite Agent Memory"
date: 2026-04-21
description: "Markdown files are useful agent notes, but durable agents need queryable memory. SQLite is a surprisingly natural fit."
author: "The Gobii Team"
seo_title: "Using SQLite Agent Memory"
seo_description: "Why SQLite works well as an agent memory layer: markdown limits, queryable state, ephemeral working tables, durable private tables, custom tools, and practical guardrails."
tags:
  - agentic
  - ai
  - sqlite
  - memory
  - architecture
---

The first version of agent memory usually looks like a folder full of markdown files: a `MEMORY.md` for durable facts, a `TODO.md` for plans, and maybe a `notes/` directory for daily logs, observations, and decisions. This is a good starting point because markdown is human-readable, easy to diff, and simple enough that both humans and models can work with it. For early agents, that visibility is a feature because you can open the files and see what the agent believes about its work.

The trouble starts when the agent stops being a chat session and starts behaving more like a small worker. A durable agent receives messages, sends replies, calls tools, scrapes pages, creates files, imports API payloads, handles attachments, and resumes jobs hours or days later. At that point, the hard part is no longer "where should I write down this thought?", it is "what state do I need to query, update, aggregate, and resume from?"

That is where markdown begins to strain. An agent can write "processed cursor abc123" into a note, but later it has to find that note, parse the prose around it, decide whether the value is still current, and update it without damaging nearby context. It can paste a JSON response into a file, but then it has to visually scan or regex its way through the blob. It can keep a running list of customers it contacted, but soon it wants to answer questions like "who got the spreadsheet, when did it deliver, and which replies had attachments?" These are database problems, not note-taking problems.

SQLite is a remarkably good answer to this class of problem. It is not glamorous, but that is part of the appeal: it is a real relational database in a single file, with indexes, transactions, joins, JSON functions, aggregates, constraints, and enough SQL to make structured memory feel precise. It does not require operating a server, and it can be copied, compressed, snapshotted, inspected, and moved around like any other file. For an agent that needs private, portable, queryable state, that shape is hard to improve on.

## Memory as Queryable State

The important shift is to stop treating memory as only prose. Prose is useful for intent, rationale, summaries, and plans, but an agent's working memory also contains records: messages, files, tool outputs, checkpoints, IDs, timestamps, statuses, and derived rows. Once memory contains records, the agent should be able to ask record-shaped questions.

Consider the difference between asking a model to reread a long transcript looking for recent inbound emails and asking SQLite for the rows directly:

```sql
SELECT message_id, timestamp, from_address, subject
FROM __messages
WHERE is_outbound = 0
  AND channel = 'email'
ORDER BY timestamp DESC
LIMIT 20;
```

The second form is not only cheaper in tokens, it is a better abstraction. The model can reason about the result while the database does the filtering and ordering, and that distinction matters because durable agents are full of small mechanical operations that should not consume model attention. Looking up a row, grouping by status, extracting a JSON field, or checking whether a cursor exists are not acts of intelligence, they are data operations.

This is the basic reason SQLite is so useful for memory: it lets the agent keep its language context for reasoning while moving structured state into a substrate designed for structured state. The prompt can carry summaries, instructions, and the current situation, while SQLite carries the rows.

## Fresh Projections and Durable Tables

The useful pattern is to combine two kinds of tables in the same database: some tables are fresh projections from the surrounding system, while other tables are private durable state created by the agent. Keeping both in SQLite gives the agent a single query interface without confusing ownership.

Runtime projection tables are rebuilt during each agent cycle and dropped before persistence. In our implementation they use `__` prefixes: `__messages`, `__tool_results`, `__files`, `__agent_config`, `__kanban_cards`, and `__agent_skills`. These tables are not the source of truth, they are a working view of the source of truth, shaped for the agent to query.

Agent-created tables are different: if the agent creates `scratch_notes`, `import_checkpoint`, `lead_research`, or `customers_seen`, those tables persist. They are part of the agent's private database, which is where the agent can build task-specific memory without requiring a new application model every time it discovers a useful structure.

That split avoids two common mistakes: treating the prompt as the only memory layer, which turns context into a dumping ground, and giving the agent direct write access to production tables, which blurs product state and working state. A private SQLite database gives the agent room to work while keeping the application database authoritative.

## Messages, Tool Results, and Files as Tables

Messages are a good example of why SQL memory matters. A long-running agent may have a lifetime of email, SMS, web chat, webhooks, and peer messages. The full record belongs in the application database, but the agent benefits from a bounded SQL working view. A `__messages` table can include message IDs, timestamps, channels, directions, sender and recipient addresses, subjects, bodies, attachment paths, rejected attachment metadata, delivery status, and latest error details.

With that table available, the agent can inspect attachments without reading a transcript:

```sql
SELECT message_id, value AS attachment_path
FROM __messages, json_each(attachment_paths_json)
ORDER BY timestamp DESC;
```

It can also summarize communication patterns directly:

```sql
SELECT channel, direction, COUNT(*) AS messages
FROM __messages
GROUP BY channel, direction
ORDER BY messages DESC;
```

This table is not for polling, since new messages already wake the agent and enter its active context. The point of the table is different: it gives the agent structured lookup over communication history when it needs IDs, status, attachments, filters, or aggregate views.

Tool results are similar. A browser result, scrape response, search result, or API payload can be too large and too structured to keep pushing through the model, so a `__tool_results` table can store text, JSON, metadata, size, truncation state, and analysis hints. The model can see a preview in context, then use SQL for the exact extraction:

```sql
SELECT
  json_extract(item.value, '$.title') AS title,
  json_extract(item.value, '$.url') AS url
FROM __tool_results,
     json_each(result_json, '$.results') AS item
WHERE result_id = 'tool_result_abc123';
```

Files also benefit from this split. File contents belong in a filespace, not duplicated into memory tables, but file metadata is perfect for SQLite. A `__files` table with paths, MIME types, sizes, checksums, and timestamps lets the agent discover and filter files before deciding what to read:

```sql
SELECT path, mime_type, size_bytes, updated_at
FROM __files
WHERE parent_path = '/exports'
ORDER BY updated_at DESC;
```

In each case, SQLite is not replacing the underlying system, it is giving the agent a queryable surface over the parts of the system it needs to reason about.

## The Agent's Own Schema

The strongest argument for SQLite memory is not the built-in tables, it is what the agent can create for itself. A durable agent often discovers the schema it needs while doing the work. It may need a checkpoint table for an import job:

```sql
CREATE TABLE IF NOT EXISTS import_checkpoint (
  source TEXT PRIMARY KEY,
  last_cursor TEXT,
  rows_seen INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

It may need a small key-value table for durable notes that should be easy to update:

```sql
CREATE TABLE IF NOT EXISTS scratch_notes (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

It may need to promote a one-time tool result into a durable research table:

```sql
CREATE TABLE lead_research AS
SELECT
  json_extract(item.value, '$.company') AS company,
  json_extract(item.value, '$.url') AS url,
  json_extract(item.value, '$.summary') AS summary
FROM __tool_results,
     json_each(result_json, '$.results') AS item
WHERE result_id = 'tool_result_abc123';
```

This is where SQLite memory becomes qualitatively different from a folder of notes. The agent is not merely appending text to remember what happened, it is creating small, task-specific data structures that it can query tomorrow. The schema can be local to the task, local to the agent, and durable enough to matter.

## Tool Results Do Not Need to Live in the Prompt

There is another important consequence: tools do not need to pass their full result back to the model every time. A large scrape, browser result, API payload, or search response can be stored in SQLite, while the model receives a shortened version with the result ID, shape, and enough preview text to decide what to do next.

If the agent needs the full result, it can query the database. If it only needs to know that the tool succeeded, how many rows came back, or which fields are available, the shortened version is enough. This keeps context focused on reasoning instead of turning every tool call into a long transcript dump.

That division of labor is the important part: tools can preserve the full data, SQLite can make it queryable, and the model can ask for the slice it needs when it needs it.

## A Better Default Than Prose Alone

Markdown still has a place. Agents should keep human-readable plans, summaries, and decisions, because prose is the right format for intent and interpretation. It just should not be the only memory substrate for a durable agent, because durable work eventually becomes structured work.

SQLite is compelling because it sits at a practical middle point: more structured than notes, lighter than a database service, easier to move than a server, and more general than a bespoke memory API. It gives an agent a private place to keep state that can be queried, joined, updated, summarized, exported, and resumed from.

The more an agent acts over time, the more memory starts to look like state. Once memory looks like state, a small database is the natural shape. SQLite happens to be the smallest useful version of that idea.
