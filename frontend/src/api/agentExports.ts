import { jsonFetch, jsonRequest } from './http'

export type PortableAgentExportScope = 'agent' | 'personal' | 'organization'
export type PortableAgentExportStatus =
  | 'queued'
  | 'running'
  | 'ready'
  | 'ready_with_warnings'
  | 'failed'
  | 'expired'

export type PortableAgentExportJob = {
  id: string
  scope: PortableAgentExportScope
  agentId: string | null
  organizationId: string | null
  formatVersion: string
  status: PortableAgentExportStatus
  phase: string
  agentsTotal: number
  agentsCompleted: number
  agentsFailed: number
  warningCount: number
  redactionCount: number
  archiveFilename: string | null
  archiveSizeBytes: number | null
  archiveSha256: string | null
  error: string | null
  createdAt: string
  completedAt: string | null
  expiresAt: string | null
  downloadUrl: string | null
}

type PortableAgentExportListResponse = {
  exports: PortableAgentExportJob[]
}

type PortableAgentExportResponse = {
  export: PortableAgentExportJob
  created?: boolean
}

const EXPORTS_URL = '/console/api/agent-exports/'

function exportQuery(scope: PortableAgentExportScope, agentId?: string | null): string {
  const params = new URLSearchParams({ scope })
  if (scope === 'agent' && agentId) {
    params.set('agentId', agentId)
  }
  return params.toString()
}

export async function fetchPortableAgentExports(
  scope: PortableAgentExportScope,
  agentId?: string | null,
  signal?: AbortSignal,
): Promise<PortableAgentExportJob[]> {
  const payload = await jsonFetch<PortableAgentExportListResponse>(
    `${EXPORTS_URL}?${exportQuery(scope, agentId)}`,
    { signal },
  )
  return payload.exports
}

export async function fetchPortableAgentExport(
  exportId: string,
  signal?: AbortSignal,
): Promise<PortableAgentExportJob> {
  const payload = await jsonFetch<PortableAgentExportResponse>(`${EXPORTS_URL}${exportId}/`, { signal })
  return payload.export
}

export function startPortableAgentExport(
  scope: PortableAgentExportScope,
  agentId?: string | null,
): Promise<PortableAgentExportResponse> {
  return jsonRequest<PortableAgentExportResponse>(EXPORTS_URL, {
    method: 'POST',
    json: {
      scope,
      ...(scope === 'agent' && agentId ? { agentId } : {}),
    },
    includeCsrf: true,
  })
}
