import { getCsrfToken, HttpError, jsonFetch, jsonRequest } from './http'
import { readStoredConsoleContext } from '../util/consoleContextStorage'

export type PortableAgentImportStatus =
  | 'validating'
  | 'awaiting_selection'
  | 'queued'
  | 'running'
  | 'completed'
  | 'completed_with_warnings'
  | 'failed'
  | 'expired'

export type PortableAgentImportItem = {
  id: string
  sourceAgentId: string
  sourceName: string
  proposedName: string
  snapshotAt: string | null
  status: 'available' | 'unavailable' | 'selected' | 'provisioning' | 'ready' | 'failed' | 'skipped'
  selectable: boolean
  messageCount: number
  stepCount: number
  fileCount: number
  warningCount: number
  warnings: string[]
  compatibility: Record<string, unknown>
  error: string | null
  importedAgent: { id: string; name: string; url: string } | null
}

export type PortableAgentImportJob = {
  id: string
  status: PortableAgentImportStatus
  phase: string
  target: { type: 'personal' | 'organization'; id: string; name: string }
  formatVersion: string | null
  archiveName: string
  archiveSizeBytes: number | null
  agentsTotal: number
  agentsSelected: number
  agentsCompleted: number
  agentsFailed: number
  warningCount: number
  capacityAvailable: number
  error: string | null
  createdAt: string
  expiresAt: string | null
  agents: PortableAgentImportItem[]
}

type ImportListResponse = { imports: PortableAgentImportJob[]; enabled: boolean }
type ImportResponse = { import: PortableAgentImportJob; created?: boolean }

const IMPORTS_URL = '/console/api/agent-imports/'

export async function fetchPortableAgentImports(signal?: AbortSignal): Promise<PortableAgentImportJob[]> {
  const payload = await jsonFetch<ImportListResponse>(IMPORTS_URL, { signal })
  return payload.imports
}

export async function fetchPortableAgentImport(importId: string, signal?: AbortSignal): Promise<PortableAgentImportJob> {
  const payload = await jsonFetch<ImportResponse>(`${IMPORTS_URL}${importId}/`, { signal })
  return payload.import
}

export function startPortableAgentImport(
  importId: string,
  agents: Array<{ itemId: string; name: string }>,
): Promise<ImportResponse> {
  return jsonRequest<ImportResponse>(`${IMPORTS_URL}${importId}/start/`, {
    method: 'POST',
    json: { agents },
    includeCsrf: true,
  })
}

export function discardPortableAgentImport(importId: string): Promise<void> {
  return jsonRequest<void>(`${IMPORTS_URL}${importId}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export function uploadPortableAgentImport(
  file: File,
  onProgress: (percent: number) => void,
): Promise<PortableAgentImportJob> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', IMPORTS_URL)
    request.responseType = 'json'
    request.setRequestHeader('Accept', 'application/json')
    request.setRequestHeader('X-CSRFToken', getCsrfToken())
    const context = readStoredConsoleContext()
    if (context) {
      request.setRequestHeader('X-Gobii-Context-Type', context.type)
      request.setRequestHeader('X-Gobii-Context-Id', context.id)
    }
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
      if (timezone) request.setRequestHeader('X-Gobii-Timezone', timezone)
    } catch {
      // The timezone header is optional.
    }
    request.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    })
    request.addEventListener('load', () => {
      const payload = request.response as ImportResponse | { error?: string } | null
      if (request.status >= 200 && request.status < 300 && payload && 'import' in payload) {
        resolve(payload.import)
        return
      }
      reject(new HttpError(request.status, request.statusText, payload))
    })
    request.addEventListener('error', () => reject(new Error('The upload could not be completed.')))
    request.addEventListener('abort', () => reject(new Error('The upload was cancelled.')))
    const body = new FormData()
    body.append('archive', file)
    request.send(body)
  })
}
