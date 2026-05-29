import { useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Loader2, Plug, Table2, Unplug } from 'lucide-react'

import {
  fetchNativeIntegrations,
  revokeNativeIntegration,
  startNativeIntegrationConnect,
  type NativeIntegrationProvider,
} from '../../api/nativeIntegrations'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { readStoredConsoleContext } from '../../util/consoleContextStorage'

type NativeAppsPanelProps = {
  listUrl: string
  onSuccess: (message: string) => void
  onError: (message: string) => void
  embedded?: boolean
}

export function NativeAppsPanel({
  listUrl,
  onSuccess,
  onError,
  embedded = false,
}: NativeAppsPanelProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['native-integrations', listUrl] as const, [listUrl])
  const integrationsQuery = useQuery({
    queryKey,
    queryFn: () => fetchNativeIntegrations(listUrl),
  })

  const connectMutation = useMutation({
    mutationFn: (provider: NativeIntegrationProvider) => startNativeIntegrationConnect(provider.connectUrl),
    onSuccess: (payload) => {
      storePendingNativeOAuth(payload.state, {
        providerKey: payload.providerKey,
        returnUrl: window.location.href,
        createdAt: Date.now(),
        context: readStoredConsoleContext(),
      })
      window.location.href = payload.authorizationUrl
    },
    onError: (error) => {
      onError(safeErrorMessage(error))
    },
  })

  const revokeMutation = useMutation({
    mutationFn: (provider: NativeIntegrationProvider) => revokeNativeIntegration(provider.revokeUrl).then(() => provider),
    onSuccess: (provider) => {
      queryClient.invalidateQueries({ queryKey })
      onSuccess(`${provider.displayName} disconnected.`)
    },
    onError: (error) => {
      onError(safeErrorMessage(error))
    },
  })

  const sectionClassName = embedded
    ? 'settings-card-surface settings-card-surface--embedded overflow-hidden rounded-xl border border-slate-200/20'
    : 'gobii-card-base overflow-hidden'
  const headerClassName = embedded
    ? 'flex flex-col gap-3 px-6 py-4 sm:flex-row sm:items-center sm:justify-between'
    : 'flex flex-col gap-3 border-b border-gray-200/70 px-6 py-4 sm:flex-row sm:items-center sm:justify-between'
  const badgeClassName = embedded
    ? 'inline-flex items-center gap-2 rounded-full border border-emerald-300/25 bg-emerald-950/45 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-emerald-100'
    : 'inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-emerald-700'
  const titleClassName = embedded ? 'text-lg font-semibold text-slate-50' : 'text-lg font-semibold text-gray-800'
  const captionClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-gray-600'
  const bodyClassName = embedded ? 'divide-y divide-slate-200/10' : 'divide-y divide-gray-200/70'
  const rowClassName = embedded
    ? 'flex flex-col gap-4 px-6 py-5 sm:flex-row sm:items-center sm:justify-between'
    : 'flex flex-col gap-4 px-6 py-5 sm:flex-row sm:items-center sm:justify-between'
  const iconShellClassName = embedded
    ? 'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-emerald-300/25 bg-emerald-950/35 text-emerald-100'
    : 'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700'
  const providerTitleClassName = embedded ? 'text-sm font-semibold text-slate-100' : 'text-sm font-semibold text-slate-900'
  const providerDescriptionClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-slate-600'
  const statusClassName = embedded
    ? 'inline-flex items-center gap-1.5 rounded-full border border-slate-200/20 bg-slate-950/25 px-2.5 py-1 text-xs font-semibold text-slate-200'
    : 'inline-flex items-center gap-1.5 rounded-full border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-700'
  const connectButtonClassName = embedded
    ? 'inline-flex items-center justify-center gap-2 rounded-lg border border-emerald-300/25 bg-emerald-900/55 px-4 py-2 text-sm font-semibold text-emerald-50 transition hover:border-emerald-200/40 hover:bg-emerald-900/75 disabled:cursor-not-allowed disabled:opacity-60'
    : 'inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60'
  const disconnectButtonClassName = embedded
    ? 'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200/20 bg-slate-950/20 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-slate-100/35 hover:bg-slate-900/40 disabled:cursor-not-allowed disabled:opacity-60'
    : 'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60'
  const loadingClassName = embedded
    ? 'flex items-center gap-2 px-6 py-8 text-sm text-slate-400'
    : 'flex items-center gap-2 px-6 py-8 text-sm text-slate-500'
  const errorClassName = embedded
    ? 'rounded-xl border border-rose-300/25 bg-rose-950/30 px-4 py-3 text-sm text-rose-100'
    : 'rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700'

  const pendingProviderKey = connectMutation.variables?.providerKey ?? revokeMutation.variables?.providerKey ?? null

  return (
    <section className={sectionClassName}>
      <div className={headerClassName}>
        <div className="space-y-1">
          <div className={badgeClassName}>
            <Plug className="h-3.5 w-3.5" aria-hidden="true" />
            Native Apps
          </div>
          <div>
            <h2 className={titleClassName}>Native apps</h2>
            <p className={captionClassName}>Connect first-party app credentials for agent API calls.</p>
          </div>
        </div>
      </div>

      {integrationsQuery.isLoading ? (
        <div className={loadingClassName}>
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading native apps...
        </div>
      ) : integrationsQuery.isError ? (
        <div className="px-6 py-5">
          <div className={errorClassName}>
            {integrationsQuery.error instanceof Error ? integrationsQuery.error.message : 'Unable to load native apps.'}
          </div>
        </div>
      ) : (
        <div className={bodyClassName}>
          {(integrationsQuery.data?.providers ?? []).map((provider) => {
            const isBusy = pendingProviderKey === provider.providerKey
            return (
              <div className={rowClassName} key={provider.providerKey}>
                <div className="flex min-w-0 gap-3">
                  <div className={iconShellClassName}>
                    <ProviderIcon provider={provider} />
                  </div>
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className={providerTitleClassName}>{provider.displayName}</h3>
                      <span className={statusClassName}>
                        {provider.connected ? (
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" aria-hidden="true" />
                        ) : (
                          <Plug className="h-3.5 w-3.5" aria-hidden="true" />
                        )}
                        {provider.connected ? 'Connected' : 'Not connected'}
                      </span>
                    </div>
                    <p className={providerDescriptionClassName}>{provider.description}</p>
                  </div>
                </div>
                <div className="flex shrink-0">
                  {provider.connected ? (
                    <button
                      type="button"
                      className={disconnectButtonClassName}
                      onClick={() => revokeMutation.mutate(provider)}
                      disabled={isBusy}
                    >
                      {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Unplug className="h-4 w-4" />}
                      Disconnect
                    </button>
                  ) : (
                    <button
                      type="button"
                      className={connectButtonClassName}
                      onClick={() => connectMutation.mutate(provider)}
                      disabled={isBusy}
                    >
                      {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}
                      Connect
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function ProviderIcon({ provider }: { provider: NativeIntegrationProvider }) {
  if (provider.icon === 'google_sheets') {
    return <Table2 className="h-5 w-5" aria-hidden="true" />
  }
  return <Plug className="h-5 w-5" aria-hidden="true" />
}

function storePendingNativeOAuth(state: string, payload: Record<string, unknown>) {
  try {
    localStorage.setItem(`gobii:native_oauth_state:${state}`, JSON.stringify(payload))
  } catch (error) {
    console.warn('Failed to persist native integration OAuth state', error)
  }
}
