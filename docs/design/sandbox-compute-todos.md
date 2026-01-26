# Sandbox Compute v1 TODOs

This list maps current implementation gaps to concrete tasks for the GKE sandbox pods spec.

## Control plane
- [ ] Implement scheduler service that creates/resumes/stops per-agent pods (gVisor RuntimeClass) and updates AgentComputeSession.state.
- [ ] Implement idle TTL sweeper (1h idle-only) to trigger sync, snapshot, and stop.
- [ ] Implement snapshot create/restore flow and write ComputeSnapshot rows.
- [ ] Add API endpoints or job hooks for terminate(reason) and error handling paths.

## Compute pods
- [ ] Replace shared sandbox-compute deployment with per-agent pods using PVCs and labels (agent_id, compute_session_id).
- [ ] Add sandbox supervisor for MCP stdio servers and tool execution (mcp_request, tool_request).
- [ ] Implement interactive exec/PTY proxy for run_command (websocket or SPDY).
- [ ] Add staff console access with audit logs and time-boxed tokens.

## Storage and sync
- [ ] Provision per-agent PVC (1Gi, RWO) and VolumeSnapshotClass.
- [ ] Enforce workspace size cap on writes and sync (1Gi hard fail).
- [ ] Finalize LWW conflict behavior on pull/push and deletion propagation (AgentFsNode.updated_at authoritative).
- [ ] Implement optional sync sidecar for continuous replication (v2).

## Networking and security
- [ ] Add NetworkPolicy to force egress only to proxy endpoints; inject proxy env.
- [ ] Ensure proxy selection is required for user/org sandboxes in prod.
- [ ] Verify sandbox pods run with RuntimeClass gvisor and restricted security context.

## MCP tooling
- [ ] Implement sandbox MCP discovery (short-lived discovery pod) and cached catalog refresh.
- [ ] Remove local fallback for non-platform MCPs once supervisor is live.

## Observability
- [ ] Emit metrics for pod startup, snapshot, sync, and tool runtime.
- [ ] Centralize sandbox audit logs (tool name, params hash, duration, exit status).
