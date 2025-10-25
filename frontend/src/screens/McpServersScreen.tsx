import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CircleHelp, CircleSlash2, Link2, Plus, Terminal } from 'lucide-react'

import {
  createMcpServer,
  deleteMcpServer,
  fetchMcpServerDetail,
  fetchMcpServers,
  type McpServer,
  type McpServerDetail,
  type McpServerListResponse,
  type McpServerPayload,
  updateMcpServer,
} from '../api/mcp'
import { HttpError } from '../api/http'
import { McpServerFormModal } from '../components/mcp/McpServerFormModal'
import { DeleteServerDialog } from '../components/mcp/DeleteServerDialog'

type FormErrors = Record<string, string[]>

type ModalState = { type: 'create' } | { type: 'edit'; serverId: string }

type McpServersScreenProps = {
  listUrl: string
  detailUrlTemplate: string
  ownerScope?: string
  ownerLabel?: string
  oauthStartUrl: string
  oauthMetadataUrl: string
  oauthCallbackPath: string
}

const PLACEHOLDER_TOKEN = '00000000-0000-0000-0000-000000000000'

export function McpServersScreen({
  listUrl,
  detailUrlTemplate,
  ownerScope,
  ownerLabel,
  oauthStartUrl,
  oauthMetadataUrl,
  oauthCallbackPath,
}: McpServersScreenProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['mcp-servers', listUrl] as const, [listUrl])
  const [modalState, setModalState] = useState<ModalState | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<McpServer | null>(null)
  const [banner, setBanner] = useState<string | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [formErrors, setFormErrors] = useState<FormErrors | null>(null)

  const { data, isLoading, isFetching, error } = useQuery<McpServerListResponse>({
    queryKey,
    queryFn: () => fetchMcpServers(listUrl),
  })

  const selectedServerId = modalState?.type === 'edit' ? modalState.serverId : null
  const detailUrl = selectedServerId ? buildUrl(detailUrlTemplate, selectedServerId) : null
  const detailQuery = useQuery<McpServerDetail>({
    queryKey: ['mcp-server-detail', detailUrl],
    queryFn: () => fetchMcpServerDetail(detailUrl!),
    enabled: Boolean(detailUrl && modalState?.type === 'edit'),
  })

  useEffect(() => {
    const handler = () => {
      queryClient.invalidateQueries({ queryKey })
    }
    document.body.addEventListener('refreshMcpServersTable', handler)
    return () => {
      document.body.removeEventListener('refreshMcpServersTable', handler)
    }
  }, [queryClient, queryKey])

  const createMutation = useMutation({
    mutationFn: (payload: McpServerPayload) => createMcpServer(listUrl, payload),
    onSuccess: () => {
      afterMutation('MCP server saved.')
    },
    onError: (err) => handleFormError(err, 'Unable to save MCP server.'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ url, payload }: { url: string; payload: McpServerPayload }) => updateMcpServer(url, payload),
    onSuccess: () => {
      afterMutation('MCP server updated.')
    },
    onError: (err) => handleFormError(err, 'Unable to update MCP server.'),
  })

  const deleteMutation = useMutation({
    mutationFn: (url: string) => deleteMcpServer(url),
    onSuccess: () => {
      setBanner('MCP server deleted.')
      setErrorBanner(null)
      setDeleteTarget(null)
      queryClient.invalidateQueries({ queryKey })
    },
    onError: (err) => handleGlobalError(err, 'Failed to delete MCP server.'),
  })

  const servers = data?.servers ?? []
  const ownerLabelText = ownerLabel || 'your workspace'
  const listError = error instanceof Error ? error.message : null
  const isSaving = createMutation.isPending || updateMutation.isPending

  const handleGlobalError = (err: unknown, fallback: string) => {
    if (err instanceof HttpError && typeof err.body === 'string' && err.body) {
      setErrorBanner(err.body)
      return
    }
    setErrorBanner(fallback)
  }

  const handleFormError = (err: unknown, fallback: string) => {
    if (err instanceof HttpError && err.status === 400 && isErrorBody(err.body)) {
      setFormErrors(err.body.errors || null)
      if (err.body.message) {
        setErrorBanner(err.body.message)
      }
      return
    }
    setFormErrors(null)
    handleGlobalError(err, fallback)
  }

  const afterMutation = (message: string) => {
    setBanner(message)
    setErrorBanner(null)
    setModalState(null)
    setFormErrors(null)
    queryClient.invalidateQueries({ queryKey })
  }

  const handleSubmit = async (payload: McpServerPayload) => {
    setFormErrors(null)
    if (modalState?.type === 'create') {
      await createMutation.mutateAsync(payload)
      return
    }
    if (modalState?.type === 'edit' && detailUrl) {
      await updateMutation.mutateAsync({ url: detailUrl, payload })
    }
  }

  const confirmDelete = () => {
    if (!deleteTarget) {
      return
    }
    const url = buildUrl(detailUrlTemplate, deleteTarget.id)
    deleteMutation.mutate(url)
  }

  const closeModal = () => {
    setModalState(null)
    setFormErrors(null)
  }

  const openCreateModal = () => {
    setFormErrors(null)
    setModalState({ type: 'create' })
  }

  const openEditModal = (serverId: string) => {
    setFormErrors(null)
    setModalState({ type: 'edit', serverId })
  }

  return (
    <div className="space-y-4">
      {banner && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">
          {banner}
        </div>
      )}
      {errorBanner && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">
          {errorBanner}
        </div>
      )}
      <div className="gobii-card-base">
        <div className="px-6 py-4 border-b border-gray-200/70 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-800">MCP Servers</h1>
            <p className="text-sm text-gray-600">Configure custom MCP servers available to {ownerLabelText}.</p>
          </div>
          <button
            type="button"
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow transition hover:bg-blue-700"
            onClick={openCreateModal}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            Add MCP Server
            {isFetching && !isLoading && <span className="text-xs font-normal text-white/80">Refreshing…</span>}
          </button>
        </div>
        {listError && (
          <div className="px-6 py-3 text-sm text-red-700 bg-red-50 border-b border-red-200">Failed to load servers. {listError}</div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full divide-y divide-gray-200/70">
            <thead className="bg-gray-50/50">
              <tr>
                <th className="px-3 md:px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
                <th className="px-3 md:px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Connection</th>
                <th className="px-3 md:px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                <th className="px-3 md:px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Updated</th>
                <th className="px-3 md:px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200/70">
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-3 md:px-6 py-6 text-center text-sm text-gray-500">
                    Loading MCP servers...
                  </td>
                </tr>
              ) : servers.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 md:px-6 py-6 text-center text-sm text-gray-500">
                    No custom MCP servers configured yet. Add one to get started.
                  </td>
                </tr>
              ) : (
                servers.map((server) => (
                  <tr className="bg-white" key={server.id}>
                    <td className="px-3 md:px-6 py-4 align-top">
                      <div className="text-sm font-semibold text-gray-900">{server.displayName}</div>
                      <div className="text-xs text-gray-500 mt-1">Identifier: {server.name}</div>
                      {server.description && <p className="mt-2 text-sm text-gray-600">{server.description}</p>}
                    </td>
                    <td className="px-3 md:px-6 py-4 align-top text-sm text-gray-700">{renderConnection(server)}</td>
                    <td className="px-3 md:px-6 py-4 align-top">
                      <span
                        className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-semibold ${
                          server.isActive ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
                        }`}
                      >
                        {server.isActive ? (
                          <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                        ) : (
                          <CircleSlash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                        )}
                        {server.isActive ? 'Active' : 'Inactive'}
                      </span>
                      <div className="text-xs text-gray-500 mt-2">{server.scopeLabel} scope</div>
                      {server.authMethod === 'oauth2' && !server.oauthConnected && (
                        <div
                          className={`mt-3 space-y-2 rounded-lg border px-3 py-2 ${
                            server.oauthPending
                              ? 'border-amber-100 bg-amber-50 text-amber-800'
                              : 'border-indigo-100 bg-indigo-50 text-indigo-800'
                          }`}
                        >
                          <p className="text-xs font-semibold">
                            {server.oauthPending ? 'Pending authorization' : 'OAuth connection required'}
                          </p>
                          <button
                            type="button"
                            className={`inline-flex items-center gap-1 rounded-md border bg-white px-2 py-1 text-xs font-semibold shadow-sm transition ${
                              server.oauthPending
                                ? 'border-amber-200 text-amber-700 hover:bg-amber-50'
                                : 'border-indigo-200 text-indigo-700 hover:bg-indigo-50'
                            }`}
                            onClick={() => openEditModal(server.id)}
                          >
                            <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
                            Connect
                          </button>
                        </div>
                      )}
                    </td>
                    <td className="px-3 md:px-6 py-4 align-top text-sm text-gray-600">
                      <div>{formatDate(server.updatedAt)}</div>
                      <div className="text-xs text-gray-400">{formatTime(server.updatedAt)}</div>
                    </td>
                    <td className="px-3 md:px-6 py-4 align-top text-right">
                      <div className="flex flex-col sm:flex-row sm:justify-end gap-2">
                        <button
                          type="button"
                          className="inline-flex items-center justify-center rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                          onClick={() => openEditModal(server.id)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="inline-flex items-center justify-center rounded-lg border border-red-200 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50"
                          onClick={() => setDeleteTarget(server)}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {modalState && (
        <McpServerFormModal
          mode={modalState.type}
          server={modalState.type === 'edit' ? detailQuery.data : undefined}
          loading={detailQuery.isLoading && modalState.type === 'edit'}
          isSubmitting={isSaving}
          ownerScope={ownerScope}
          onClose={closeModal}
          onSubmit={handleSubmit}
          errorResponse={formErrors}
          oauth={{
            startUrl: oauthStartUrl,
            metadataUrl: oauthMetadataUrl,
            callbackPath: oauthCallbackPath,
          }}
        />
      )}

      {deleteTarget && (
        <DeleteServerDialog
          serverName={deleteTarget.displayName}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={confirmDelete}
          isDeleting={deleteMutation.isPending}
        />
      )}
    </div>
  )
}

function buildUrl(template: string, id: string): string {
  if (!template) {
    return ''
  }
  if (template.includes(PLACEHOLDER_TOKEN)) {
    return template.replace(PLACEHOLDER_TOKEN, id)
  }
  return `${template}${id}`
}

function renderConnection(server: McpServer) {
  if (server.command) {
    return (
      <div className="space-y-2">
        <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-700">
          <Terminal className="h-3.5 w-3.5" aria-hidden="true" />
          Command
        </span>
        <p className="break-all font-mono text-xs text-gray-600">{server.command}</p>
        {server.commandArgs.length > 0 && (
          <p className="break-all font-mono text-xs text-gray-500">Args: {server.commandArgs.join(' ')}</p>
        )}
      </div>
    )
  }
  if (server.url) {
    const scheme = server.url.trim().toLowerCase().startsWith('https') ? 'HTTPS' : 'HTTP'
    return (
      <div className="space-y-2">
        <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-semibold text-indigo-700">
          <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
          {scheme}
        </span>
        <p className="break-all font-mono text-xs text-gray-600">{server.url}</p>
      </div>
    )
  }
  return (
    <p className="inline-flex items-center gap-2 text-xs text-gray-500">
      <CircleHelp className="h-4 w-4" aria-hidden="true" />
      No connection settings provided.
    </p>
  )
}

function formatDate(iso: string): string {
  const value = iso ? new Date(iso) : null
  if (!value || Number.isNaN(value.getTime())) {
    return 'Unknown'
  }
  return value.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatTime(iso: string): string {
  const value = iso ? new Date(iso) : null
  if (!value || Number.isNaN(value.getTime())) {
    return ''
  }
  return value.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

function isErrorBody(payload: unknown): payload is { errors?: FormErrors; message?: string } {
  if (!payload || typeof payload !== 'object') {
    return false
  }
  const record = payload as Record<string, unknown>
  return 'errors' in record || 'message' in record
}
