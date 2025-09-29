# React Persistent Agent Web UI Plan

## 1. Inventory From `2025-09-18-webui`

### 1.1 Layout & Templates
- `console/templates/console/agent_workspace.html` provides the full-page layout: timeline shell, load older/newer controls, processing indicator slot, jump-to-latest button, composer form, and hidden cursor metadata nodes. Markup is tightly coupled to HTMX (`hx-post`, `hx-swap`).
- Partial templates under `console/templates/console/partials/` render timeline content:
  - `_agent_timeline_items.html` groups events via the `group_timeline_events` filter and delegates to message/step partials.
  - `_timeline_message_card.html`/`_timeline_message_card_content.html` render chat bubbles with channel badges, attachments, and agent/user theming.
  - `_timeline_step_cluster.html` renders grouped tool calls with collapsible chip list, per-tool iconography, detail drawers, and SQL/parameter rendering via `_timeline_step_details.html` and `_timeline_step_details_body.html`.
  - `agent_timeline_window.html`/`agent_timeline_delta.html` wrap the event list and output `hx-swap-oob` fragments for cursor metadata, load controls, jump button, and processing flags.
- Other console partials (`_agent_list.html`, `_talk_to_agent_modal.html`, etc.) supply ancillary UI like agent selection but follow similar Tailwind-inspired utility class styling.

### 1.2 Styling
- `static/css/agent_workspace.css` (~700 lines) defines bespoke styles for the timeline, chat bubbles, tool clusters, composer, jump button positioning, and processing indicator animations. It assumes specific DOM ids/classes from the Django templates.
- Styling mixes utility-class expectations (Tailwind-like class names baked into markup) with custom rules for layout (`#timeline-events`, `.tool-cluster`, `.chat-bubble--user`, etc.), responsive tweaks, and Prism themes for syntax highlighting.

### 1.3 Client-Side Behaviour (`static/js/agent_workspace.js`)
- 2.4k lines of imperative JS orchestrating:
  - HTMX lifecycle management for pagination (`timeline-fetch` action, `hx-trigger`, `hx-swap-oob` updates) and composer form submissions.
  - Server Sent Events (SSE) via `EventSource` for `message.created`, `step.created`, `processing.started`, `processing.finished`. SSE payloads only signal “new data available”; actual data fetched via HTMX delta requests.
  - Auto-scroll heuristics with jump-to-latest button when the user scrolls up, preserving scroll anchors when history loads, and aligning timeline bottom padding with composer size.
  - Processing indicator lifecycle, including suppression when the latest segment isn’t mounted, sticky behaviour during reconnects, and fade transitions.
  - Tool cluster merging, collapse thresholds, chip toggling, detail drawers, and SQL highlighting (Prism integration).
  - Web session management (`agent_web_session` endpoint) with start/heartbeat/end workflow to keep long-poll/SSE resources alive.
  - Timeline cursor bookkeeping, backlog queueing, resync detection, empty-state handling, and Prism re-highlighting after swaps.

### 1.4 Django Views & Helpers
- `AgentWorkspaceView` seeds initial context (timeline window, cursor bounds, `event_stream_url`, `processing_status_url`, `web_session_url`, TTL).
- `AgentTimelineWindowView` serves both full snapshots and directional deltas (older/newer) with cursor validation logic; deltas rely on HTMX out-of-band swaps for UI chrome.
- `AgentWebMessageView` handles composer posts (HTML response with HX triggers).
- `AgentWebSessionView` and `AgentProcessingStatusView` expose JSON APIs for session keepalive and processing status polls.
- `console/timeline.py` constructs timeline windows (messages + steps) with cursor encoding, history checks, and oversampling.
- `console/templatetags/agent_extras.py` supplies:
  - `agent_message_html` sanitisation/markdown rendering.
  - `group_timeline_events` (clusters steps, injects tool metadata, collapse thresholds).
  - `tool_metadata`, `channel_label`, and helpers for parameter/result display.

### 1.5 Feature Summary To Preserve
- Message bubbles with sanitised HTML/markdown, attachment chips, channel badges, and relative timestamps.
- Tool call batches with icons, captions derived from tool params, collapsible chip list, detail drawers showing JSON/SQL/charter updates.
- Bidirectional pagination (older/newer), jump-to-latest affordance, intelligent auto-scroll.
- Real-time updates signalling agent processing state and new events.
- Processing indicator pill tied to agent first name.
- Web session/presence semantics (avoid multiple tabs fighting for connection).
- Empty state for untouched timelines, merging of contiguous tool clusters, Prism highlighting.

## 2. Rewrite Goals (React + WebSockets)
1. Client-rendered experience within `frontend/` React app, mounted at `/console/agents/:id/chat/`.
2. Replace HTMX/SSE with a Channels WebSocket delivering concrete event payloads (no server-rendered HTML).
3. Serve timeline history and pagination via JSON REST endpoints (cursor-based) for symmetry with WebSocket events.
4. Preserve UX richness (tool batching, attachments, processing indicator, jump button, syntax highlighting) while simplifying control flow.
5. Establish a single state source that both REST bootstrap and WebSocket delta updates feed into, keeping React tree declarative.
6. Retain web session semantics if still required for concurrency limits, or fold into socket auth if redundant.

## 3. Proposed Architecture

### 3.1 Data Flow
- **Initial load**: React screen `AgentChatShellScreen` (or new route component) fetches `GET /api/console/agents/:id/timeline?limit=...` returning timeline window JSON, cursor metadata, processing status, agent info, and maybe tool metadata enums.
- **Pagination**: Same endpoint with `direction=older|newer&cursor=` to fetch additional slices. Server returns `events` array plus `cursors` object { oldest, newest, hasMoreOlder, hasMoreNewer }.
- **Real-time**: WebSocket channel `ws://.../ws/agents/:id/` emits structured events:
  - `message.created`, `step.created` with payload (event cursor, serialized message/step, computed cluster group id).
  - `processing.started` / `processing.finished` with timestamps.
  - Optional `timeline.resync` when backend detects cursor drift.
- **Composer**: POST `POST /api/console/agents/:id/messages` for user messages; response echoes saved message to allow optimistic update (or rely on WebSocket broadcast).
- **Session**: Either
  - Keep existing web session API but call from React (if backend still needs TTL gating), or
  - Authenticate WebSocket handshake and treat socket presence as the session (heartbeats built into WS). Decision pending operations requirements.

### 3.2 State Management (Zustand Decision)
- Recommend **Zustand** store to centralize timeline state, because multiple components (timeline list, load controls, jump button, processing indicator, composer) need consistent access to:
  - `events` keyed by cursor/id.
  - `clusters` derived from step events.
  - Cursor metadata / pagination flags.
  - Auto-scroll preference and page offset tracking.
  - Processing status, queue of pending deltas, connection status.
- Zustand’s simple store with selectors keeps implementation light, avoids prop drilling, and plays nicely with WebSocket callbacks. React Query could coexist for REST fetching, but Zustand will hold authoritative timeline slice. Include devtools in development only.

### 3.3 Event Normalisation & Grouping
- Port `console/timeline.group_timeline_events` logic to TypeScript utility so client can group events consistently (start from JSON that includes event type + tool metadata).
- Alternatively, extend REST/WS payloads to return pre-grouped structures (e.g., cluster id + summary). Chosen approach: backend provides **raw events + tool metadata**; frontend groups to enable richer interactions (and avoids duplicating cluster state between REST and WS). Need JSON schema for message events and step events (with tool name, params, results, tokens, etc.).
- Provide shared metadata map from backend (labels, icons) to avoid hardcoding in JS; expose via API or ship static JSON bundle.

### 3.4 Pagination & Autoscroll Strategy
- Store `oldestCursor`, `newestCursor`, `hasMoreOlder`, `hasMoreNewer` in Zustand.
- Pagination actions:
  - `loadOlder()` fetches REST slice, prepends to list, preserves scroll anchor using measured heights before/after render (React `useLayoutEffect` + `ResizeObserver`).
  - `loadNewer()` appends and optionally toggles auto-scroll.
- Auto-scroll: track whether user is “pinned to bottom” (within threshold). When pinned, new events animate in and view scrolls down; otherwise show “Jump to latest” floating button.

### 3.5 Processing Indicator & Activity State
- Zustand slice `processing: { active: boolean, since?: timestamp, sticky?: boolean }`.
- WebSocket `processing.*` events update this state. REST bootstrap includes current processing flag.
- React component `ProcessingIndicator` renders pill (markup copied) when `active`, with fade transitions handled via CSS classes; hidden otherwise.

### 3.6 Tool Clusters & Detail Panels
- Represent tool clusters as derived state: consecutive step events with cluster-friendly tools merge into `ToolCluster` object with `entries`, icons, earliest/latest timestamps, collapse threshold (default 5).
- Implement toggling using React state per cluster or store-managed currently open cluster id.
- Tool chip detail panel replicates existing markup; content uses `<pre>` for JSON/SQL (Prism highlight via `react-syntax-highlighter` or load Prism manually).

### 3.7 Message Rendering & Sanitisation
- Continue to rely on backend to provide sanitised HTML (use existing `agent_message_html` to pre-render or expose markdown + sanitized HTML). Plan: timeline API responds with `rendered_html` and raw `body`. React uses `dangerouslySetInnerHTML` with sanitized output and plain text fallback for attachments.
- Attachments delivered as array with `{ id, filename, url, file_size }` for direct linking.
- Channel badges rendered conditionally (Web vs others) using metadata from backend.

### 3.8 WebSocket Lifecycle & Error Handling
- Connection managed via dedicated hook `useAgentWebSocket(agentId)` that:
  - Handles JWT/session cookie authentication.
  - Reconnects exponentially on failure, updates store with `connectionStatus`.
  - Buffers incoming events if the user has pending backlog (e.g., scrolled up) and flushes when re-synced.
- When server requests resync (`timeline.resync`), trigger `loadSnapshot()` to refetch full window.
- Optionally integrate with web session API: start session before opening socket, schedule heartbeat via `setInterval`, end session on unmount/tab close.

### 3.9 Processing of Older/Newer History
- Convert existing Django view to `application/json`; update URLs to something like `/api/console/agents/:id/timeline/window` (GET) returning shape:
  ```json
  {
    "events": [ ... ],
    "cursors": { "oldest": "...", "newest": "...", "hasMoreOlder": true, "hasMoreNewer": false },
    "processing": { "active": true, "since": "2025-09-29T14:32:00Z" },
    "agent": { "id": "...", "name": "...", "firstName": "..." }
  }
  ```
- Include `timelineMode` (snapshot/delta) if helpful, though React code can treat directional fetches explicitly.

### 3.10 Syntax Highlighting
- Keep Prism for continuity (load on-demand), or switch to lightweight highlighter. Plan: lazy-load Prism CSS/JS once the timeline mounts and re-run on message/cluster detail render.

## 4. Component Breakdown (Initial Skeleton)
- `AgentChatPage` (route-level) – fetch bootstrap data, mount providers, render layout.
- `AgentChatStore` (Zustand) – holds timeline, cursors, processing, connection state, preferences.
- `AgentChatLayout` – overall shell (header, timeline, composer positioning).
- `TimelineViewport` – scroll container managing virtualisation threshold, auto-scroll detection, jump button.
- `TimelineEventList` – renders grouped events (messages vs tool clusters) from store selector.
- `MessageEventCard` – markup for agent/user bubble, attachments, timestamp.
- `ToolClusterCard` – cluster summary, chip list, toggle state, detail host.
- `ToolChipPanel` – detail drawer with parameter/result rendering (SQL/JSON components).
- `ProcessingIndicator` – floating pill referencing store.
- `JumpToLatestButton` – floating action toggled by store state.
- `Composer` – textarea, attachments (future), send button; handles optimistic send + resetting auto-scroll.
- `LoadControl` – top/bottom “Load older/newer” button(s), hidden when exhausted.
- Hooks: `useTimelineAutoScroll`, `usePagination`, `useClusterInteractions`, `useAgentWebSession` (if web session API retained).

## 5. Styling Strategy
- Introduce `frontend/src/styles/agentChat.css` composed largely of the extracted rules from `static/css/agent_workspace.css`. Maintain class names to minimise churn, with TODO comments for future Tailwind migration.
- Wrap timeline components with deterministic classNames (`timeline-event`, `tool-cluster`, etc.) so CSS applies without major rewrites.
- Use CSS variables for paddings as legacy code did; convert to React-managed inline style updates where necessary (e.g., bottom padding adjustments executed via style props or CSS variables on container).

## 6. Server-Side Work
1. **API endpoints**
   - `GET /api/console/agents/:id/timeline`: accept `cursor`, `direction`, `limit` → JSON window. Reuse `fetch_timeline_window` logic, but serialise events (message + step) and include tool metadata map.
   - `POST /api/console/agents/:id/messages`: accept `{ body, subject? }`, return saved message payload.
   - `GET /api/console/agents/:id/processing`: (optional) remains for polling fallback.
2. **WebSocket channel**
   - Channel layer group per agent; broadcast message/step events with serialised payloads (existing Channels stack already in place with Redis).
   - Emit processing state transitions; include heartbeat/resync instructions.
   - Authenticate via session or token; ensure permission parity with current console views.
3. **Serialisers**
   - Create DRF serializers or custom dict builders mirroring template data (message metadata, attachments, tool results).
   - Provide tool metadata definitions (icon paths, colors) either inline with events or via separate endpoint to avoid duplication.
4. **Sanitisation**
   - Reuse `agent_message_html` to pre-render safe HTML stored in payload.
   - Provide formatted timestamps (ISO + humanized string for convenience).
5. **Session management**
   - Decide whether to keep `agent_web_session` endpoints; if yes, expose to React app and call on mount/unmount; if no, remove and ensure backend clean-up relies on socket lifecycle.

## 7. Implementation Phases
1. **Scaffolding & Data Contracts**
   - Finalise REST/WS payload schema; document in OpenAPI or shared TypeScript types.
   - Implement serializers + provisional endpoints returning mock data for front-end development.
2. **React Skeleton**
   - Add Zustand store, provider hook, and initial `AgentChatPage` hooking into stub endpoints.
   - Render message + tool cluster components using stub data (leveraging markup copied in this change).
3. **Pagination & Auto-scroll**
   - Implement `loadOlder`/`loadNewer`, scroll anchor preservation, jump button logic.
   - Integrate actual REST backend for history slices.
4. **WebSocket Integration**
   - Hook up real-time channel, manage connection state, queue deltas when timeline not at bottom, request resync on drift.
5. **Processing Indicator & Presence**
   - Wire `processing` events, fallback to periodic REST poll if socket offline.
   - Decide on session heartbeat; implement whichever pattern emerges.
6. **Polish**
   - Syntax highlighting, attachment handling, tool detail panels, keyboard shortcuts (e.g., `Ctrl+Enter` send), empty state.
   - Analytics/telemetry hooks replacing the manual console logs.
7. **Migration Clean-up**
   - Remove legacy HTMX templates/JS/CSS once React screen proven.
   - Update navigation/linking to point to new React route.

## 8. Open Questions & Risks
- **Web session necessity**: Does Channels/WebSocket connection make the dedicated web session API redundant? Clarify to avoid duplicating heartbeats.
- **Tool metadata source of truth**: Prefer a backend-provided map to keep icon/label definitions consistent between Python (`agent_extras.py`) and React.
- **Cluster grouping location**: Confirm whether grouping should remain server-side (for consistency/performance) or move fully client-side. Current plan assumes client grouping with shared metadata; re-evaluate after defining API payloads.
- **Prism vs alternative highlighter**: Determine acceptable bundle size; Prism via CDN was previously used.
- **Large timelines**: Evaluate need for virtualised list (e.g. `react-virtuoso`) if event volume grows.
- **Concurrent tabs**: Legacy session management enforced one active UI; ensure new approach respects same constraints if required.
- **Accessibility review**: Ensure focus management on cluster toggles and jump button meets expectations.

