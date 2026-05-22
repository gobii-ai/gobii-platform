import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CircleHelp, CircleSlash2, Link2, Plus, Terminal } from 'lucide-react'

import {
  fetchMcpServers,
  type McpServer,
  type McpServerListResponse,
} from '../api/mcp'
import { McpServerFormModal } from '../components/mcp/McpServerFormModal'
import { AssignServerModal } from '../components/mcp/AssignServerModal'
import { DeleteServerDialog } from '../components/mcp/DeleteServerDialog'
import { McpServerTestModal } from '../components/mcp/McpServerTestModal'
import { PipedreamAppsPanel } from '../components/mcp/PipedreamAppsPanel'
import { useModal } from '../hooks/useModal'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'

type McpServersScreenProps = {
  listUrl: string
  detailUrlTemplate: string
  assignmentUrlTemplate: string
  testUrlTemplate: string
  ownerScope?: string
  ownerLabel?: string
  allowCommands?: boolean
  pipedreamAppsUrl?: string | null
  pipedreamAppSearchUrl?: string | null
  oauthStartUrl: string
  oauthMetadataUrl: string
  oauthCallbackPath: string
  variant?: 'standalone' | 'embedded'
}

const PLACEHOLDER_TOKEN = '00000000-0000-0000-0000-000000000000'

export function McpServersScreen({
  listUrl,
  detailUrlTemplate,
  assignmentUrlTemplate,
  testUrlTemplate,
  ownerScope,
  ownerLabel,
  allowCommands = false,
  pipedreamAppsUrl = null,
  pipedreamAppSearchUrl = null,
  oauthStartUrl,
  oauthMetadataUrl,
  oauthCallbackPath,
  variant = 'standalone',
}: McpServersScreenProps) {
  const isEmbedded = variant === 'embedded'
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['mcp-servers', listUrl] as const, [listUrl])
  const [modal, showModal] = useModal()
  const [banner, setBanner] = useState<string | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const { data, isLoading, isFetching, error } = useQuery<McpServerListResponse>({
    queryKey,
    queryFn: () => fetchMcpServers(listUrl),
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

  const servers = data?.servers ?? []
  const resolvedOwnerScope = ownerScope ?? data?.ownerScope
  const ownerLabelText = resolvedOwnerScope === 'platform' ? 'the platform' : ownerLabel || data?.ownerLabel || 'your workspace'
  const listError = error instanceof Error ? error.message : null

  const handleSuccess = useCallback(
    (message: string) => {
      setBanner(message)
      setErrorBanner(null)
      queryClient.invalidateQueries({ queryKey })
    },
    [queryClient, queryKey],
  )

  const handleError = useCallback((message: string) => {
    setErrorBanner(message)
    setBanner(null)
  }, [])

  const openCreateModal = useCallback(() => {
    showModal((onClose) => (
      <McpServerFormModal
        mode="create"
        listUrl={listUrl}
        ownerScope={resolvedOwnerScope}
        allowCommands={allowCommands}
        onClose={onClose}
        onSuccess={handleSuccess}
        onError={handleError}
        oauth={{
          startUrl: oauthStartUrl,
          metadataUrl: oauthMetadataUrl,
          callbackPath: oauthCallbackPath,
        }}
      />
    ))
  }, [
    showModal,
    listUrl,
    resolvedOwnerScope,
    allowCommands,
    handleSuccess,
    handleError,
    oauthStartUrl,
    oauthMetadataUrl,
    oauthCallbackPath,
  ])

  const openEditModal = useCallback(
    (server: McpServer) => {
      const detailUrl = buildUrl(detailUrlTemplate, server.id)
      showModal((onClose) => (
        <McpServerFormModal
          mode="edit"
          listUrl={listUrl}
          detailUrl={detailUrl}
          ownerScope={resolvedOwnerScope}
          allowCommands={allowCommands}
          onClose={onClose}
          onSuccess={handleSuccess}
          onError={handleError}
          oauth={{
            startUrl: oauthStartUrl,
            metadataUrl: oauthMetadataUrl,
            callbackPath: oauthCallbackPath,
          }}
        />
      ))
    },
    [
      showModal,
      detailUrlTemplate,
      listUrl,
      resolvedOwnerScope,
      allowCommands,
      handleSuccess,
      handleError,
      oauthStartUrl,
      oauthMetadataUrl,
      oauthCallbackPath,
    ],
  )

  const openAssignModal = useCallback(
    (server: McpServer) => {
      if (server.scope === 'platform') {
        return
      }
      const assignmentUrl = buildUrl(assignmentUrlTemplate, server.id)
      showModal((onClose) => (
        <AssignServerModal
          server={server}
          assignmentUrl={assignmentUrl}
          onClose={onClose}
          onSuccess={(message) => handleSuccess(message || 'MCP server assignments updated.')}
          onError={handleError}
        />
      ))
    },
    [showModal, assignmentUrlTemplate, handleSuccess, handleError],
  )

  const openDeleteModal = useCallback(
    (server: McpServer) => {
      const deleteUrl = buildUrl(detailUrlTemplate, server.id)
      showModal((onClose) => (
        <DeleteServerDialog
          serverName={server.displayName}
          deleteUrl={deleteUrl}
          onClose={onClose}
          onDeleted={() => handleSuccess('MCP server deleted.')}
          onError={handleError}
        />
      ))
    },
    [showModal, detailUrlTemplate, handleSuccess, handleError],
  )

  const openTestModal = useCallback(
    (server: McpServer) => {
      const testUrl = buildUrl(testUrlTemplate, server.id)
      const assignmentUrl = buildUrl(assignmentUrlTemplate, server.id)
      showModal((onClose) => (
        <McpServerTestModal
          server={server}
          testUrl={testUrl}
          assignmentUrl={assignmentUrl}
          requiresAgent={requiresSandboxAgent(server)}
          onClose={onClose}
          onError={handleError}
        />
      ))
    },
    [showModal, testUrlTemplate, assignmentUrlTemplate, handleError],
  )

  const rootClassName = isEmbedded ? 'space-y-5' : 'space-y-4'
  const successBannerClassName = isEmbedded
    ? 'rounded-xl border border-emerald-300/25 bg-emerald-950/30 px-4 py-2 text-sm text-emerald-100'
    : 'rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800'
  const errorBannerClassName = isEmbedded
    ? 'rounded-xl border border-rose-300/25 bg-rose-950/30 px-4 py-2 text-sm text-rose-100'
    : 'rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800'
  const tableShellClassName = isEmbedded
    ? 'settings-card-surface settings-card-surface--embedded overflow-hidden rounded-xl border border-slate-200/20'
    : 'gobii-card-base'
  const headerClassName = isEmbedded
    ? 'px-6 py-4 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between'
    : 'px-6 py-4 border-b border-gray-200/70 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between'
  const headingClassName = isEmbedded ? 'text-2xl font-semibold text-slate-50' : 'text-2xl font-semibold text-gray-800'
  const descriptionClassName = isEmbedded ? 'text-sm text-slate-400' : 'text-sm text-gray-600'
  const primaryButtonClassName = isEmbedded
    ? 'inline-flex items-center justify-center gap-2 rounded-lg border border-sky-300/25 bg-sky-900/55 px-4 py-2 text-sm font-semibold text-sky-50 transition hover:border-sky-200/40 hover:bg-sky-900/75'
    : 'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow transition hover:bg-blue-700'
  const listErrorClassName = isEmbedded
    ? 'mx-6 mb-4 rounded-xl border border-rose-300/25 bg-rose-950/30 px-4 py-3 text-sm text-rose-100'
    : 'px-6 py-3 text-sm text-red-700 bg-red-50 border-b border-red-200'
  const tableClassName = isEmbedded ? 'w-full' : 'w-full divide-y divide-gray-200/70'
  const tableHeadClassName = isEmbedded ? 'bg-slate-950/40' : 'bg-gray-50/50'
  const tableBodyClassName = isEmbedded ? 'divide-y divide-slate-200/10' : 'divide-y divide-gray-200/70'
  const thClassName = isEmbedded
    ? 'px-3 md:px-6 py-3 text-left text-xs font-medium text-slate-400 uppercase tracking-wider'
    : 'px-3 md:px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider'
  const thRightClassName = `${thClassName} text-right`
  const emptyCellClassName = isEmbedded
    ? 'px-3 md:px-6 py-6 text-center text-sm text-slate-400'
    : 'px-3 md:px-6 py-6 text-center text-sm text-gray-500'
  const rowClassName = isEmbedded ? 'bg-transparent' : 'bg-white'
  const cellClassName = 'px-3 md:px-6 py-4 align-top'
  const titleClassName = isEmbedded ? 'text-sm font-semibold text-slate-100' : 'text-sm font-semibold text-gray-900'
  const metaClassName = isEmbedded ? 'text-xs text-slate-500 mt-1' : 'text-xs text-gray-500 mt-1'
  const bodyTextClassName = isEmbedded ? 'mt-2 text-sm text-slate-400' : 'mt-2 text-sm text-gray-600'
  const secondaryTextClassName = isEmbedded ? 'text-sm text-slate-300' : 'text-sm text-gray-700'
  const dateClassName = isEmbedded ? 'px-3 md:px-6 py-4 align-top text-sm text-slate-400' : 'px-3 md:px-6 py-4 align-top text-sm text-gray-600'
  const timeClassName = isEmbedded ? 'text-xs text-slate-500' : 'text-xs text-gray-400'
  const assignButtonClassName = isEmbedded
    ? 'inline-flex items-center justify-center rounded-lg border border-sky-300/25 bg-sky-950/20 px-3 py-2 text-sm font-medium text-sky-100 transition hover:border-sky-200/40 hover:bg-sky-900/40'
    : 'inline-flex items-center justify-center rounded-lg border border-indigo-200 px-3 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50'
  const editButtonClassName = isEmbedded
    ? 'inline-flex items-center justify-center rounded-lg border border-slate-200/20 bg-slate-950/20 px-3 py-2 text-sm font-medium text-slate-200 transition hover:border-slate-100/35 hover:bg-slate-900/40'
    : 'inline-flex items-center justify-center rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50'
  const testButtonClassName = isEmbedded
    ? 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-emerald-300/25 bg-emerald-950/20 px-3 py-2 text-sm font-medium text-emerald-100 transition hover:border-emerald-200/40 hover:bg-emerald-900/35 disabled:cursor-not-allowed disabled:opacity-50'
    : 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-emerald-200 px-3 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50'
  const deleteButtonClassName = isEmbedded
    ? 'inline-flex items-center justify-center rounded-lg border border-rose-300/25 bg-rose-950/20 px-3 py-2 text-sm font-medium text-rose-200 transition hover:border-rose-200/40 hover:bg-rose-900/35'
    : 'inline-flex items-center justify-center rounded-lg border border-red-200 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50'

  return (
    <div className={rootClassName}>
      {isEmbedded ? (
        <SettingsBanner
          variant="embedded"
          eyebrow="Workspace"
          title="Integrations"
          subtitle={`Configure custom MCP servers available to ${ownerLabelText}.`}
        />
      ) : null}
      {banner && (
        <div className={successBannerClassName}>
          {banner}
        </div>
      )}
      {errorBanner && (
        <div className={errorBannerClassName}>
          {errorBanner}
        </div>
      )}
      {pipedreamAppsUrl && pipedreamAppSearchUrl ? (
        <PipedreamAppsPanel
          settingsUrl={pipedreamAppsUrl}
          searchUrl={pipedreamAppSearchUrl}
          onSuccess={handleSuccess}
          onError={handleError}
          embedded={isEmbedded}
        />
      ) : null}
      <div className={tableShellClassName}>
        <div className={headerClassName}>
          {!isEmbedded ? (
            <div>
              <h1 className={headingClassName}>MCP Servers</h1>
              <p className={descriptionClassName}>Configure custom MCP servers available to {ownerLabelText}.</p>
            </div>
          ) : (
            <div>
              <h2 className={headingClassName}>MCP Servers</h2>
              <p className={descriptionClassName}>Manage server connections and agent assignments.</p>
            </div>
          )}
          <button
            type="button"
            className={primaryButtonClassName}
            onClick={openCreateModal}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            Add MCP Server
            {isFetching && !isLoading && <span className="text-xs font-normal text-white/80">Refreshing…</span>}
          </button>
        </div>
        {listError && (
          <div className={listErrorClassName}>Failed to load servers. {listError}</div>
        )}
        <div className="overflow-x-auto">
          <table className={tableClassName}>
            <thead className={tableHeadClassName}>
              <tr>
                <th className={thClassName}>Name</th>
                <th className={thClassName}>Connection</th>
                <th className={thClassName}>Status</th>
                <th className={thClassName}>Updated</th>
                <th className={thRightClassName}>Actions</th>
              </tr>
            </thead>
            <tbody className={tableBodyClassName}>
              {isLoading ? (
                <tr>
                  <td colSpan={5} className={emptyCellClassName}>
                    Loading MCP servers...
                  </td>
                </tr>
              ) : servers.length === 0 ? (
                <tr>
                  <td colSpan={5} className={emptyCellClassName}>
                    No custom MCP servers configured yet. Add one to get started.
                  </td>
                </tr>
              ) : (
                servers.map((server) => (
                  <tr className={rowClassName} key={server.id}>
                    <td className={cellClassName}>
                      <div className={titleClassName}>{server.displayName}</div>
                      <div className={metaClassName}>Identifier: {server.name}</div>
                      {server.description && <p className={bodyTextClassName}>{server.description}</p>}
                    </td>
                    <td className={`${cellClassName} ${secondaryTextClassName}`}>{renderConnection(server, isEmbedded)}</td>
                    <td className={cellClassName}>
                      <span
                        className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-semibold ${
                          isEmbedded
                            ? server.isActive
                              ? 'border border-emerald-300/25 bg-emerald-950/35 text-emerald-200'
                              : 'border border-slate-200/20 bg-slate-900/45 text-slate-400'
                            : server.isActive ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
                        }`}
                      >
                        {server.isActive ? (
                          <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                        ) : (
                          <CircleSlash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                        )}
                        {server.isActive ? 'Active' : 'Inactive'}
                      </span>
                      {server.authMethod === 'oauth2' && !server.oauthConnected && (
                        <div
                          className={`mt-3 space-y-2 rounded-lg border px-3 py-2 ${
                            isEmbedded
                              ? server.oauthPending
                                ? 'border-amber-300/25 bg-amber-950/30 text-amber-100'
                                : 'border-sky-300/25 bg-sky-950/30 text-sky-100'
                              : server.oauthPending
                                ? 'border-amber-100 bg-amber-50 text-amber-800'
                                : 'border-indigo-100 bg-indigo-50 text-indigo-800'
                          }`}
                        >
                          <p className="text-xs font-semibold">
                            {server.oauthPending ? 'Pending authorization' : 'OAuth connection required'}
                          </p>
                          <button
                            type="button"
                            className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold transition ${
                              isEmbedded
                                ? server.oauthPending
                                  ? 'border-amber-300/30 bg-amber-950/25 text-amber-100 hover:bg-amber-900/35'
                                  : 'border-sky-300/30 bg-sky-950/25 text-sky-100 hover:bg-sky-900/35'
                                : server.oauthPending
                                  ? 'border-amber-200 bg-white text-amber-700 shadow-sm hover:bg-amber-50'
                                  : 'border-indigo-200 bg-white text-indigo-700 shadow-sm hover:bg-indigo-50'
                            }`}
                            onClick={() => openEditModal(server)}
                          >
                            <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
                            Connect
                          </button>
                        </div>
                      )}
                    </td>
                    <td className={dateClassName}>
                      <div>{formatDate(server.updatedAt)}</div>
                      <div className={timeClassName}>{formatTime(server.updatedAt)}</div>
                    </td>
                    <td className={`${cellClassName} text-right`}>
                      <div className="flex flex-col sm:flex-row sm:justify-end gap-2">
                        {server.scope !== 'platform' && (
                          <button
                            type="button"
                            className={assignButtonClassName}
                            onClick={() => openAssignModal(server)}
                          >
                            Assign Agents
                          </button>
                        )}
                        <button
                          type="button"
                          className={testButtonClassName}
                          onClick={() => openTestModal(server)}
                          disabled={!server.isActive}
                          title={!server.isActive ? 'Activate this MCP server before testing.' : undefined}
                        >
                          Test
                        </button>
                        <button
                          type="button"
                          className={editButtonClassName}
                          onClick={() => openEditModal(server)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className={deleteButtonClassName}
                          onClick={() => openDeleteModal(server)}
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

      {modal}
    </div>
  )
}

function requiresSandboxAgent(server: McpServer): boolean {
  return server.scope !== 'platform' && Boolean(server.command) && !server.url
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

function renderConnection(server: McpServer, embedded = false) {
  const commandBadgeClassName = embedded
    ? 'inline-flex items-center gap-1 rounded-full border border-slate-200/20 bg-slate-900/45 px-2 py-0.5 text-xs font-semibold text-slate-200'
    : 'inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-700'
  const urlBadgeClassName = embedded
    ? 'inline-flex items-center gap-1 rounded-full border border-sky-300/25 bg-sky-950/45 px-2 py-0.5 text-xs font-semibold text-sky-100'
    : 'inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-semibold text-indigo-700'
  const monoTextClassName = embedded ? 'break-all font-mono text-xs text-slate-400' : 'break-all font-mono text-xs text-gray-600'
  const monoMutedClassName = embedded ? 'break-all font-mono text-xs text-slate-500' : 'break-all font-mono text-xs text-gray-500'
  const emptyClassName = embedded ? 'inline-flex items-center gap-2 text-xs text-slate-500' : 'inline-flex items-center gap-2 text-xs text-gray-500'

  if (server.command) {
    return (
      <div className="space-y-2">
        <span className={commandBadgeClassName}>
          <Terminal className="h-3.5 w-3.5" aria-hidden="true" />
          Command
        </span>
        <p className={monoTextClassName}>{server.command}</p>
        {server.commandArgs.length > 0 && (
          <p className={monoMutedClassName}>Args: {server.commandArgs.join(' ')}</p>
        )}
      </div>
    )
  }
  if (server.url) {
    const scheme = server.url.trim().toLowerCase().startsWith('https') ? 'HTTPS' : 'HTTP'
    return (
      <div className="space-y-2">
        <span className={urlBadgeClassName}>
          <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
          {scheme}
        </span>
        <p className={monoTextClassName}>{server.url}</p>
      </div>
    )
  }
  return (
    <p className={emptyClassName}>
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
