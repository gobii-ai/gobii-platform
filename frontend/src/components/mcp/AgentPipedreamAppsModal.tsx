import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Loader2, Plug, Search, Sparkles, Trash2, Unplug } from 'lucide-react'

import {
  disconnectAgentPipedreamApp,
  fetchAgentPipedreamApps,
  removeAgentPipedreamApp,
  startAgentPipedreamAppConnect,
  type AgentPipedreamAppRow,
} from '../../api/mcp'
import { AgentChatMobileSheet } from '../agentChat/AgentChatMobileSheet'
import { Modal } from '../common/Modal'
import { PipedreamAppIcon, resolvePipedreamAppsErrorMessage } from './PipedreamAppsShared'

type AgentPipedreamAppsModalProps = {
  agentId: string
  onClose: () => void
}

type PendingAction = {
  slug: string
  kind: 'connect' | 'disconnect' | 'remove'
} | null

export function AgentPipedreamAppsModal({ agentId, onClose }: AgentPipedreamAppsModalProps) {
  const queryClient = useQueryClient()
  const [isMobile, setIsMobile] = useState(false)
  const [searchTerm, setSearchTerm] = useState('')
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState('')
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)

  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 768)
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebouncedSearchTerm(searchTerm.trim()), 250)
    return () => window.clearTimeout(timeoutId)
  }, [searchTerm])

  const appsQueryKey = useMemo(
    () => ['agent-pipedream-apps', agentId, debouncedSearchTerm] as const,
    [agentId, debouncedSearchTerm],
  )

  const appsQuery = useQuery({
    queryKey: appsQueryKey,
    queryFn: () => fetchAgentPipedreamApps(agentId, debouncedSearchTerm),
  })

  useEffect(() => {
    const handleFocus = () => {
      void appsQuery.refetch()
    }
    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [appsQuery])

  const connectMutation = useMutation({
    mutationFn: (app: AgentPipedreamAppRow) => startAgentPipedreamAppConnect(agentId, app.slug),
    onMutate: (app) => {
      setPendingAction({ slug: app.slug, kind: 'connect' })
      setStatusMessage(null)
    },
    onSuccess: (result, app) => {
      window.open(result.connectUrl, '_blank', 'noopener,noreferrer')
      setStatusMessage(`Connect ${app.name} in the new tab, then return here.`)
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-settings'], exact: false })
      void appsQuery.refetch()
    },
    onError: (error) => {
      setStatusMessage(resolvePipedreamAppsErrorMessage(error, 'Unable to start connection.'))
    },
    onSettled: () => setPendingAction(null),
  })

  const disconnectMutation = useMutation({
    mutationFn: (app: AgentPipedreamAppRow) => disconnectAgentPipedreamApp(agentId, app.slug),
    onMutate: (app) => {
      setPendingAction({ slug: app.slug, kind: 'disconnect' })
      setStatusMessage(null)
    },
    onSuccess: (_result, app) => {
      setStatusMessage(`${app.name} disconnected.`)
      void appsQuery.refetch()
    },
    onError: (error) => {
      setStatusMessage(resolvePipedreamAppsErrorMessage(error, 'Unable to disconnect app.'))
    },
    onSettled: () => setPendingAction(null),
  })

  const removeMutation = useMutation({
    mutationFn: (app: AgentPipedreamAppRow) => removeAgentPipedreamApp(agentId, app.slug),
    onMutate: (app) => {
      setPendingAction({ slug: app.slug, kind: 'remove' })
      setStatusMessage(null)
    },
    onSuccess: (_result, app) => {
      setStatusMessage(`${app.name} removed.`)
      void queryClient.invalidateQueries({ queryKey: ['pipedream-app-settings'], exact: false })
      void appsQuery.refetch()
    },
    onError: (error) => {
      setStatusMessage(resolvePipedreamAppsErrorMessage(error, 'Unable to remove app.'))
    },
    onSettled: () => setPendingAction(null),
  })

  const apps = appsQuery.data?.apps ?? []
  const isBusy = connectMutation.isPending || disconnectMutation.isPending || removeMutation.isPending

  const body = (
    <div className="space-y-4 p-1">
      {statusMessage ? (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          {statusMessage}
        </div>
      ) : null}

      <label className="relative block text-sm text-slate-500">
        <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
          {appsQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" aria-hidden="true" />}
        </span>
        <input
          type="search"
          className="w-full rounded-lg border border-slate-300 bg-white py-3 pl-10 pr-3 text-sm text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-blue-500"
          placeholder="Search apps"
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          disabled={isBusy}
        />
      </label>

      {appsQuery.isError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {resolvePipedreamAppsErrorMessage(appsQuery.error, 'Unable to load apps.')}
        </div>
      ) : appsQuery.isLoading ? (
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-5 text-sm text-slate-600">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading apps…
        </div>
      ) : apps.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-5 text-sm text-slate-600">
          No apps matched your search.
        </div>
      ) : (
        <div className={`overflow-hidden rounded-lg border border-slate-200 bg-white ${isMobile ? '' : 'max-h-[28rem] overflow-y-auto'}`}>
          <div className="divide-y divide-slate-200">
            {apps.map((app) => (
              <AgentPipedreamAppRowItem
                key={app.slug}
                app={app}
                pendingAction={pendingAction}
                disabled={isBusy}
                onConnect={() => connectMutation.mutate(app)}
                onDisconnect={() => disconnectMutation.mutate(app)}
                onRemove={() => removeMutation.mutate(app)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )

  if (isMobile) {
    return (
      <AgentChatMobileSheet
        open
        onClose={onClose}
        title="Apps"
        subtitle="Search, connect, and disconnect apps for this agent."
        icon={Sparkles}
        ariaLabel="Manage agent apps"
        bodyPadding={false}
      >
        <div className="h-full min-h-0 overflow-y-auto overscroll-contain px-4 pb-6 pt-4">
          {body}
        </div>
      </AgentChatMobileSheet>
    )
  }

  return (
    <Modal
      title="Apps"
      subtitle="Search, connect, and disconnect apps for this agent."
      onClose={onClose}
      widthClass="sm:max-w-5xl"
      icon={Sparkles}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-700"
    >
      {body}
    </Modal>
  )
}

function AgentPipedreamAppRowItem({
  app,
  pendingAction,
  disabled,
  onConnect,
  onDisconnect,
  onRemove,
}: {
  app: AgentPipedreamAppRow
  pendingAction: PendingAction
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  onRemove: () => void
}) {
  const isPending = pendingAction?.slug === app.slug
  const pendingKind = isPending ? pendingAction?.kind : null
  const removeDisabled = disabled || app.source !== 'added'
  const removeTitle = app.source === 'built_in'
    ? 'Built-in apps cannot be removed'
    : app.source === 'available'
      ? 'Connect or add this app before removing it'
      : 'Remove app'

  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_7rem_8rem_7rem] md:items-center">
      <div className="flex min-w-0 items-center gap-3">
        <PipedreamAppIcon app={app} />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">{app.name}</p>
          {app.description ? <p className="mt-1 line-clamp-2 text-sm text-slate-600">{app.description}</p> : null}
        </div>
      </div>
      <div>
        {app.connected ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            Connected
          </span>
        ) : (
          <span className="inline-flex rounded-full border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-500">
            Not connected
          </span>
        )}
      </div>
      <div className="flex justify-start md:justify-end">
        {app.connected ? (
          <button
            type="button"
            className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-50 disabled:opacity-60"
            onClick={onDisconnect}
            disabled={disabled}
          >
            {pendingKind === 'disconnect' ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Unplug className="h-4 w-4" aria-hidden="true" />
            )}
            Disconnect
          </button>
        ) : (
          <button
            type="button"
            className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
            onClick={onConnect}
            disabled={disabled}
          >
            {pendingKind === 'connect' ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Plug className="h-4 w-4" aria-hidden="true" />
            )}
            Connect
          </button>
        )}
      </div>
      <div className="flex justify-start md:justify-end">
        <button
          type="button"
          className="inline-flex min-w-24 items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-45"
          onClick={onRemove}
          disabled={removeDisabled}
          title={removeTitle}
        >
          {pendingKind === 'remove' ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          )}
          Remove
        </button>
      </div>
    </div>
  )
}
