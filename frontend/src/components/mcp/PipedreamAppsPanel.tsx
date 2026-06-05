import { useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, Loader2, Plus, Sparkles } from 'lucide-react'

import { fetchPipedreamAppSettings, type PipedreamAppSummary } from '../../api/mcp'
import { fetchNativeIntegrations, type NativeIntegrationProvider } from '../../api/nativeIntegrations'
import { InlineStatusBanner } from '../common/InlineStatusBanner'
import { getSettingsSurfaceClassName } from '../common/SettingsSurface'
import { useModal } from '../../hooks/useModal'
import { NativeProviderIcon } from './NativeIntegrationShared'
import { PipedreamAppsModal } from './PipedreamAppsModal'
import { PipedreamAppIcon, resolvePipedreamAppsErrorMessage } from './PipedreamAppsShared'

type PipedreamAppsPanelProps = {
  settingsUrl?: string | null
  searchUrl?: string | null
  nativeIntegrationsUrl?: string | null
  onError: (message: string) => void
  embedded?: boolean
}

export function PipedreamAppsPanel({
  settingsUrl,
  searchUrl,
  nativeIntegrationsUrl = null,
  onError,
  embedded = false,
}: PipedreamAppsPanelProps) {
  const [modal, showModal] = useModal()
  const queryKey = useMemo(() => ['pipedream-app-settings', settingsUrl] as const, [settingsUrl])
  const settingsQuery = useQuery({
    queryKey,
    queryFn: () => fetchPipedreamAppSettings(settingsUrl as string),
    enabled: Boolean(settingsUrl && searchUrl),
  })
  const nativeQueryKey = useMemo(
    () => ['native-integrations', nativeIntegrationsUrl] as const,
    [nativeIntegrationsUrl],
  )
  const nativeIntegrationsQuery = useQuery({
    queryKey: nativeQueryKey,
    queryFn: () => fetchNativeIntegrations(nativeIntegrationsUrl as string),
    enabled: Boolean(nativeIntegrationsUrl),
  })

  const emptySettings = useMemo(() => ({
    ownerScope: '',
    ownerLabel: '',
    platformApps: [],
    selectedApps: [],
    effectiveApps: [],
  }), [])
  const effectiveSettings = settingsQuery.data ?? emptySettings
  const hasPipedreamApps = Boolean(settingsUrl && searchUrl)
  const canOpenModal = hasPipedreamApps ? Boolean(settingsQuery.data) : Boolean(nativeIntegrationsUrl)

  const openModal = useCallback(() => {
    if (!hasPipedreamApps && !nativeIntegrationsUrl) {
      return
    }
    if (hasPipedreamApps && !settingsQuery.data) {
      return
    }
    showModal((onClose) => (
      <PipedreamAppsModal
        settingsUrl={settingsUrl ?? null}
        searchUrl={searchUrl ?? null}
        nativeIntegrationsUrl={nativeIntegrationsUrl}
        initialSettings={effectiveSettings}
        onClose={onClose}
        onError={onError}
      />
    ))
  }, [
    effectiveSettings,
    hasPipedreamApps,
    nativeIntegrationsUrl,
    onError,
    searchUrl,
    settingsQuery.data,
    settingsUrl,
    showModal,
  ])

  const sectionClassName = embedded
    ? getSettingsSurfaceClassName({ variant: 'embedded', roundedClassName: 'rounded-xl' })
    : 'gobii-card-base overflow-hidden'
  const headerClassName = embedded
    ? 'flex flex-col gap-4 px-6 py-4 sm:flex-row sm:items-center sm:justify-between'
    : 'flex flex-col gap-4 border-b border-gray-200/70 px-6 py-4 sm:flex-row sm:items-center sm:justify-between'
  const badgeClassName = embedded
    ? 'inline-flex items-center gap-2 rounded-full border border-sky-300/25 bg-sky-950/45 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-sky-100'
    : 'inline-flex items-center gap-2 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-blue-700'
  const titleClassName = embedded ? 'text-lg font-semibold text-slate-50' : 'text-lg font-semibold text-gray-800'
  const buttonClassName = embedded
    ? 'inline-flex items-center justify-center gap-2 rounded-lg border border-sky-300/25 bg-sky-900/55 px-4 py-2 text-sm font-semibold text-sky-50 transition hover:border-sky-200/40 hover:bg-sky-900/75 disabled:opacity-60'
    : 'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow transition hover:bg-blue-700 disabled:opacity-60'
  const loadingClassName = embedded
    ? 'flex items-center gap-2 px-6 py-8 text-sm text-slate-400'
    : 'flex items-center gap-2 px-6 py-8 text-sm text-slate-500'

  return (
    <>
      <section className={sectionClassName}>
        <div className={headerClassName}>
          <div className="space-y-1">
            <div className={badgeClassName}>
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              Apps
            </div>
            <div>
              <h2 className={titleClassName}>Additional apps</h2>
            </div>
          </div>
          <button
            type="button"
            className={buttonClassName}
            onClick={openModal}
            disabled={!canOpenModal || settingsQuery.isLoading || nativeIntegrationsQuery.isLoading}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            Add Apps
          </button>
        </div>

        {(hasPipedreamApps && settingsQuery.isLoading) || nativeIntegrationsQuery.isLoading ? (
          <div className={loadingClassName}>
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading apps…
          </div>
        ) : (hasPipedreamApps && settingsQuery.isError) || nativeIntegrationsQuery.isError ? (
          <div className="px-6 py-5">
            <InlineStatusBanner variant="error" surface={embedded ? 'embedded' : 'standalone'}>
              {resolvePipedreamAppsErrorMessage(settingsQuery.error ?? nativeIntegrationsQuery.error, 'Unable to load apps right now.')}
            </InlineStatusBanner>
          </div>
        ) : (
          <div className={`grid gap-6 px-6 py-5 ${nativeIntegrationsUrl && hasPipedreamApps ? 'lg:grid-cols-3' : 'lg:grid-cols-2'}`}>
            {nativeIntegrationsUrl ? (
              <NativeAppColumn
                title="Native apps"
                caption="Connected at the workspace level."
                providers={nativeIntegrationsQuery.data?.providers ?? []}
                embedded={embedded}
              />
            ) : null}
            {hasPipedreamApps ? (
              <>
            <AppColumn
              title="Included apps"
              caption="Available automatically for this workspace."
              apps={effectiveSettings.platformApps}
              emptyText="No included apps configured."
              tone="platform"
              embedded={embedded}
            />
            <AppColumn
              title="Your apps"
              caption="Additional apps enabled for your Agents"
              apps={effectiveSettings.selectedApps}
              emptyText="No additional apps enabled yet."
              tone="selected"
              embedded={embedded}
            />
              </>
            ) : null}
          </div>
        )}
      </section>
      {modal}
    </>
  )
}

function NativeAppColumn({
  title,
  caption,
  providers,
  embedded = false,
}: {
  title: string
  caption: string
  providers: NativeIntegrationProvider[]
  embedded?: boolean
}) {
  const titleClassName = embedded ? 'text-sm font-semibold text-slate-100' : 'text-sm font-semibold text-slate-900'
  const captionClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-slate-600'
  const connectedClassName = embedded
    ? 'border-emerald-300/25 bg-emerald-950/45 text-emerald-100'
    : 'border-emerald-200 bg-emerald-50 text-emerald-700'
  const disconnectedClassName = embedded
    ? 'border-slate-200/20 bg-slate-900/45 text-slate-200'
    : 'border-slate-200 bg-white text-slate-700'
  const emptyClassName = embedded
    ? 'rounded-lg border border-dashed border-slate-200/20 bg-slate-950/25 px-4 py-4 text-sm text-slate-400'
    : 'rounded-lg border border-dashed border-slate-200 bg-white px-4 py-4 text-sm text-slate-600'

  return (
    <div className="space-y-3">
      <div>
        <h3 className={titleClassName}>{title}</h3>
        <p className={captionClassName}>{caption}</p>
      </div>
      {providers.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {providers.map((provider) => (
            <span
              key={provider.providerKey}
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm font-medium ${
                provider.connected ? connectedClassName : disconnectedClassName
              }`}
            >
              <NativeProviderIcon provider={provider} />
              <span>{provider.displayName}</span>
              {provider.connected ? <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" /> : null}
            </span>
          ))}
        </div>
      ) : (
        <div className={emptyClassName}>
          No native apps configured.
        </div>
      )}
    </div>
  )
}

function AppColumn({
  title,
  caption,
  apps,
  emptyText,
  tone,
  embedded = false,
}: {
  title: string
  caption: string
  apps: PipedreamAppSummary[]
  emptyText: string
  tone: 'platform' | 'selected'
  embedded?: boolean
}) {
  const accentClass =
    embedded
      ? tone === 'platform'
        ? 'border-slate-200/20 bg-slate-900/45 text-slate-200'
        : 'border-sky-300/25 bg-sky-950/45 text-sky-100'
      : tone === 'platform'
        ? 'border-slate-200 bg-slate-50 text-slate-700'
        : 'border-blue-200 bg-blue-50 text-blue-700'
  const titleClassName = embedded ? 'text-sm font-semibold text-slate-100' : 'text-sm font-semibold text-slate-900'
  const captionClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-slate-600'
  const appNameClassName = embedded
    ? tone === 'platform' ? 'text-slate-100' : 'text-sky-50'
    : tone === 'platform' ? 'text-slate-800' : 'text-blue-900'
  const emptyClassName = embedded
    ? 'rounded-lg border border-dashed border-slate-200/20 bg-slate-950/25 px-4 py-4 text-sm text-slate-400'
    : 'rounded-lg border border-dashed border-slate-200 bg-slate-50/60 px-4 py-4 text-sm text-slate-600'

  return (
    <div className="space-y-3">
      <div>
        <h3 className={titleClassName}>{title}</h3>
        <p className={captionClassName}>{caption}</p>
      </div>
      {apps.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {apps.map((app) => (
            <span
              key={app.slug}
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm font-medium ${accentClass}`}
            >
              <PipedreamAppIcon app={app} size="sm" />
              <span className={appNameClassName}>{app.name}</span>
            </span>
          ))}
        </div>
      ) : (
        <div className={emptyClassName}>
          {emptyText}
        </div>
      )}
    </div>
  )
}
