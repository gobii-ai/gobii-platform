# Sandbox Compute (Kubernetes Backend) Notes

## Settings
- SANDBOX_COMPUTE_BACKEND: set to "kubernetes" to enable per-agent pods.
- SANDBOX_COMPUTE_POD_IMAGE: sandbox supervisor image (default ghcr.io/gobii-ai/gobii-sandbox-compute:main).
- SANDBOX_COMPUTE_K8S_NAMESPACE: namespace for per-agent pods (default in-cluster namespace).
- SANDBOX_COMPUTE_PVC_SIZE: workspace PVC size (default 1Gi).
- SANDBOX_COMPUTE_PVC_STORAGE_CLASS / SANDBOX_COMPUTE_SNAPSHOT_CLASS: storage/snapshot class names.
- SANDBOX_COMPUTE_POD_CONFIGMAP_NAME / SANDBOX_COMPUTE_POD_SECRET_NAME: env sources for pods.
- SANDBOX_COMPUTE_POD_READY_TIMEOUT_SECONDS / SANDBOX_COMPUTE_SNAPSHOT_TIMEOUT_SECONDS: readiness timeouts.

## RBAC requirements
The control-plane service account must be able to:
- pods: get/list/watch/create/delete
- pods/proxy: create
- persistentvolumeclaims: get/list/watch/create/delete
- volumesnapshots.snapshot.storage.k8s.io: get/list/watch/create/delete

## Resource naming
- Pods: sandbox-agent-<agent_uuid>
- PVCs: sandbox-workspace-<agent_uuid>
- Snapshots: sandbox-snap-<agent_prefix>-<timestamp>

## Lifecycle
- Idle sweeper syncs workspace, snapshots PVC, deletes pod, and deletes PVC on success.
- Resume creates PVC from snapshot (when present) and recreates the pod.
