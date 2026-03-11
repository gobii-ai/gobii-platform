import { useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Plus, Sparkles } from 'lucide-react'

import { fetchPipedreamAppSettings, type PipedreamAppSummary } from '../../api/mcp'
import { useModal } from '../../hooks/useModal'
import { PipedreamAppsModal } from './PipedreamAppsModal'

type PipedreamAppsPanelProps = {
  settingsUrl: string
  searchUrl: string
  onSuccess: (message: string) => void
  onError: (message: string) => void
}

export function PipedreamAppsPanel({
  settingsUrl,
  searchUrl,
  onSuccess,
  onError,
}: PipedreamAppsPanelProps) {
  const [modal, showModal] = useModal()
  const queryKey = useMemo(() => ['pipedream-app-settings', settingsUrl] as const, [settingsUrl])
  const settingsQuery = useQuery({
    queryKey,
    queryFn: () => fetchPipedreamAppSettings(settingsUrl),
  })

  const openModal = useCallback(() => {
    if (!settingsQuery.data) {
      return
    }
    showModal((onClose) => (
      <PipedreamAppsModal
        settingsUrl={settingsUrl}
        searchUrl={searchUrl}
        initialSettings={settingsQuery.data}
        onClose={onClose}
        onSuccess={onSuccess}
        onError={onError}
      />
    ))
  }, [onError, onSuccess, searchUrl, settingsQuery.data, settingsUrl, showModal])

  return (
    <>
      <section className="gobii-card-base overflow-hidden">
        <div className="flex flex-col gap-4 border-b border-gray-200/70 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <div className="inline-flex items-center gap-2 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-blue-700">
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              Apps
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-800">Additional apps</h2>
            </div>
          </div>
          <button
            type="button"
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow transition hover:bg-blue-700 disabled:opacity-60"
            onClick={openModal}
            disabled={!settingsQuery.data || settingsQuery.isLoading}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            Add Apps
          </button>
        </div>

        {settingsQuery.isLoading ? (
          <div className="flex items-center gap-2 px-6 py-8 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading apps…
          </div>
        ) : settingsQuery.isError ? (
          <div className="px-6 py-5">
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {resolveErrorMessage(settingsQuery.error, 'Unable to load apps right now.')}
            </div>
          </div>
        ) : settingsQuery.data ? (
          <div className="grid gap-6 px-6 py-5 lg:grid-cols-[1.2fr_1fr]">
            <AppColumn
              title="Included apps"
              caption="Available automatically for this workspace."
              apps={settingsQuery.data.platformApps}
              emptyText="No included apps configured."
              tone="platform"
            />
            <AppColumn
              title="Added apps"
              caption="Additional apps enabled for this owner context."
              apps={settingsQuery.data.selectedApps}
              emptyText="No additional apps enabled yet."
              tone="selected"
            />
          </div>
        ) : null}
      </section>
      {modal}
    </>
  )
}

function AppColumn({
  title,
  caption,
  apps,
  emptyText,
  tone,
}: {
  title: string
  caption: string
  apps: PipedreamAppSummary[]
  emptyText: string
  tone: 'platform' | 'selected'
}) {
  const accentClass =
    tone === 'platform'
      ? 'border-slate-200 bg-slate-50 text-slate-700'
      : 'border-blue-200 bg-blue-50 text-blue-700'

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        <p className="text-sm text-slate-600">{caption}</p>
      </div>
      {apps.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {apps.map((app) => (
            <span
              key={app.slug}
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm font-medium ${accentClass}`}
            >
              <AppIcon app={app} />
              <span className={tone === 'platform' ? 'text-slate-800' : 'text-blue-900'}>{app.name}</span>
            </span>
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50/60 px-4 py-4 text-sm text-slate-600">
          {emptyText}
        </div>
      )}
    </div>
  )
}

function AppIcon({ app }: { app: PipedreamAppSummary }) {
  if (app.iconUrl) {
    return (
      <img
        src={app.iconUrl}
        alt=""
        className="h-6 w-6 rounded-lg border border-slate-200 bg-white object-cover"
        loading="lazy"
      />
    )
  }
  return (
    <span className="inline-flex h-6 w-6 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-[10px] font-semibold uppercase text-slate-700">
      {app.name.slice(0, 2)}
    </span>
  )
}

function resolveErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return fallback
}
