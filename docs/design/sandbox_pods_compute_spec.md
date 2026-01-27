# GKE Sandbox Pods Compute Service Spec

Status: Draft
Owner: Platform

## Goals
- Run user-added stdio MCP servers in per-agent sandboxed compute pods.
- Run non-MCP sandboxed tools (e.g., create_file) in the same pod.
- Provide interactive, long-lived compute sessions with idle-only TTL.
- Persist compute state across idle expiry using disk-only snapshots.
- Sync the agent filespace bidirectionally with last-writer-wins.
- Route all pod egress through the existing proxy system.
- Allow staff console access with strong audit logging.
- Enable agents to write and execute arbitrary Python code in the sandbox.

## Non-goals
- GUI-level computer use (CLI only in v1).
- Memory snapshots or process-state resume in v1.
- Running platform MCP servers inside the pod.

## Constraints (Confirmed)
- One sandboxed pod per agent.
- Idle TTL: 1 hour (idle-only).
- Workspace size cap: 1 GB, hard-fail on writes beyond cap.
- Filespace sync: bidirectional, last-writer-wins.
- Network: proxy-only egress.
- Platform MCP servers are trusted and remain outside the pod.
- User/org MCP servers and sandboxed tools always execute inside the pod.

## Architecture Overview

### Control Plane (in GKE)
- Compute API service used by Gobii agents and tools.
- Metadata store for session state and snapshots.
- Scheduler that creates, resumes, or stops per-agent pods.
- Egress proxy controller that creates per-agent egress proxy pods and services.

### Compute Pods (in GKE)
- GKE sandbox pods using gVisor (RuntimeClass: gvisor).
- One pod per agent, running a sandbox tool supervisor.
- Interactive session via exec/PTY proxy.

### Egress Proxy Pods (in GKE)
- One proxy pod per agent, configured with the selected Decodo upstream (host/port/creds).
- Exposes a per-agent ClusterIP service (e.g., `sandbox-egress-<agent_id>`).
- Sandbox pods send all outbound HTTP/S traffic to the per-agent proxy service.

### Storage
- Per-agent PVC for workspace (1 GB cap).
- VolumeSnapshot used on idle stop for fast resume.
- Snapshot metadata stored in the control-plane DB.

### Networking
- NetworkPolicy enforces egress only to proxy endpoints.
- Pod environment provides proxy settings for all tools.
- Control plane selects a healthy Decodo proxy from the DB and wires the per-agent proxy pod.
- Sandbox pod `HTTP_PROXY/HTTPS_PROXY` point at the per-agent proxy service; the proxy pod connects to Decodo.

## Kubernetes Primitives

### RuntimeClass
- runtimeClassName: gvisor
- Node pool must enable sandboxed workloads.

### Pod (per agent)
- Labels: agent_id, compute_session_id
- Containers:
  - sandbox-supervisor (main)
  - optional sync-sidecar (if using continuous sync)
- Volumes:
  - workspace PVC mounted at /workspace

### Pod (per-agent egress proxy)
- Labels: agent_id, compute_session_id, app=sandbox-egress-proxy
- Container:
  - egress-proxy (connects to Decodo upstream defined by env)
- Service:
  - ClusterIP per agent (e.g., `sandbox-egress-<agent_id>`)

### PVC
- Size: 1Gi
- Access mode: ReadWriteOnce
- Bound to a single pod at a time

### VolumeSnapshot
- Triggered on idle stop
- Used to restore workspace on resume

## Data Model (Control Plane)

### AgentComputeSession
- agent_id (UUID, PK)
- pod_name
- namespace
- state: running | idle_stopping | stopped | error
- last_activity_at
- lease_expires_at
- workspace_snapshot_id
- created_at, updated_at

### ComputeSnapshot
- snapshot_id (UUID, PK)
- agent_id
- k8s_snapshot_name
- size_bytes (best-effort)
- created_at
- status: ready | failed

## Sandbox Tool Supervisor (in Pod)
- Launches stdio MCP servers defined by the agent.
- Executes non-MCP sandboxed tools (e.g., create_file) inside the pod.
- Provides a python_exec tool for running arbitrary Python code within the sandbox.
- Exposes MCP and tool execution over HTTP or WebSocket to the control plane.
- Restarts MCP servers on failure.
- Records tool execution logs to a local audit log file.

## Compute API (Control Plane)

### deploy_or_resume
- Input: agent_id
- Output: session state, endpoint info
- Behavior:
  - If pod running, return session info.
  - If stopped, restore from latest VolumeSnapshot.
  - If none, create new pod + fresh PVC.

### run_command
- Input: agent_id, command, cwd, env, timeout, interactive=true
- Output: exit_code, stdout, stderr
- Behavior:
  - Executes in pod using exec/PTY if interactive.

### mcp_request
- Input: agent_id, server_id, tool_name, params
- Output: MCP tool response
- Behavior:
  - Routed to the sandbox tool supervisor inside the pod.

### tool_request
- Input: agent_id, tool_name, params
- Output: tool response
- Behavior:
  - Routed to the sandbox tool supervisor inside the pod.
  - Includes sandboxed tools like create_file and python_exec.

### sync_filespace
- Input: agent_id, direction=push|pull
- Output: sync summary
- Behavior:
  - Bidirectional merge with last-writer-wins.

### terminate
- Input: agent_id, reason
- Output: final status
- Behavior:
  - Force stop, snapshot optional based on reason.

## Filespace Sync
- Sync on:
  - Pod create/resume (pull from filespace into workspace)
  - Tool call or MCP request completion (push back)
  - Idle stop (final push)
- Conflict resolution: last-writer-wins using AgentFsNode.updated_at as the authoritative clock.
- Sync algorithm (authoritative):
  - On push: the sync service detects changed workspace files and writes them to AgentFsNode, setting updated_at to the sync timestamp.
  - On pull: when a workspace file conflicts, the AgentFsNode with the newest updated_at wins.
  - Deletions propagate only if the workspace change is newer than the AgentFsNode updated_at.
- Hard-fail if workspace exceeds 1 GB before sync.

## MCP Tool Discovery and Caching
- Cache tool definitions for user/org stdio MCP servers whenever possible.
- Prefer cached catalogs over waking idle pods for discovery.
- Populate/refresh cached catalogs on MCP server config create/update using a short-lived sandbox discovery pod.
- Refresh cached catalogs only when MCP server configs change or on explicit refresh.

## Idle TTL and Snapshot Flow
- Any tool call, MCP request, run_command, or console access resets last_activity_at.
- Idle TTL is 1 hour; when exceeded:
  1) Sync workspace to filespace (push).
  2) Create VolumeSnapshot for the PVC.
  3) Stop pod.
- Resume:
  1) Restore PVC from latest VolumeSnapshot.
  2) Sync filespace into workspace (pull).
  3) Start MCP supervisor.

## Security and Isolation
- User/org MCP servers must run in the sandboxed pod.
- User/org sandboxed tools must run in the sandboxed pod.
- Platform MCP servers remain in the trusted worker process.
- Control plane enforces:
  - If MCP server scope != platform (command or url), route to pod.
- gVisor provides stronger isolation than standard containers.

## python_exec Policy
- Default timeout: 30s (configurable per request, hard cap 120s).
- Max stdout/stderr per call: 1 MB combined (truncate beyond cap).
- CPU/memory limits inherit the pod resource limits.

## Observability and Audit
- Control plane logs:
  - Pod lifecycle events, snapshot create/restore, sync results.
- Sandbox supervisor logs:
  - tool_name, params hash, duration, exit status.
- Metrics:
  - Pod startup time, snapshot duration, sync duration, tool runtime.

## Staff Console Access
- Staff-only, time-boxed access tokens.
- Required reason string for every session.
- Audit log captures:
  - staff_id, agent_id, connect/disconnect timestamps
  - command stream (truncated), stdout/stderr
  - snapshot id at time of access

## Failure Handling
- Snapshot failure:
  - Keep pod stopped, mark session error, retain last good snapshot.
- Sync failure:
  - Retry once; on failure, stop pod and mark error.
- Node failure:
  - Session marked error; resume on another node from last snapshot.

## Rollout Notes
- v1: disk-only snapshots, no memory restore.
- v2: optional file sync sidecar for continuous replication.
