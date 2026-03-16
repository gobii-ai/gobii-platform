import { useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'

import { fetchPipedreamAppSettings } from '../../api/mcp'
import type { PipedreamAppSummary } from '../../api/mcp'
import { useModal } from '../../hooks/useModal'
import { PipedreamAppsModal } from '../mcp/PipedreamAppsModal'
import { PipedreamAppIcon } from '../mcp/PipedreamAppsShared'

const INLINE_PRIORITY = ['linkedin', 'google_sheets', 'trello', 'slack']
const MAX_INLINE_APPS = 3

type ComposerPipedreamAppsControlProps = {
  settingsUrl: string
  searchUrl: string
  disabled?: boolean
}

export function ComposerPipedreamAppsControl({
  settingsUrl,
  searchUrl,
  disabled = false,
}: ComposerPipedreamAppsControlProps) {
  const [modal, showModal] = useModal()
  const settingsQuery = useQuery({
    queryKey: ['pipedream-app-settings', settingsUrl],
    queryFn: () => fetchPipedreamAppSettings(settingsUrl),
  })

  const inlineApps = useMemo(() => {
    const selectedApps = settingsQuery.data?.selectedApps ?? []
    const effectiveApps = settingsQuery.data?.effectiveApps ?? []
    const effectiveBySlug = new Map(effectiveApps.map((app) => [app.slug, app] as const))
    const orderedApps: PipedreamAppSummary[] = []
    const seen = new Set<string>()

    selectedApps.forEach((app) => {
      if (seen.has(app.slug)) {
        return
      }
      seen.add(app.slug)
      orderedApps.push(app)
    })

    INLINE_PRIORITY
      .map((slug) => effectiveBySlug.get(slug))
      .filter((app): app is PipedreamAppSummary => Boolean(app))
      .forEach((app) => {
        if (seen.has(app.slug)) {
          return
        }
        seen.add(app.slug)
        orderedApps.push(app)
      })

    effectiveApps.forEach((app) => {
      if (seen.has(app.slug)) {
        return
      }
      seen.add(app.slug)
      orderedApps.push(app)
    })

    return orderedApps.slice(0, MAX_INLINE_APPS)
  }, [settingsQuery.data?.effectiveApps, settingsQuery.data?.selectedApps])

  const openModal = useCallback(() => {
    if (!settingsQuery.data || disabled) {
      return
    }
    showModal((onClose) => (
      <PipedreamAppsModal
        settingsUrl={settingsUrl}
        searchUrl={searchUrl}
        initialSettings={settingsQuery.data}
        onClose={onClose}
        onSuccess={() => {}}
        onError={() => {}}
      />
    ))
  }, [disabled, searchUrl, settingsQuery.data, settingsUrl, showModal])

  const triggerDisabled = disabled || settingsQuery.isLoading || !settingsQuery.data

  return (
    <>
      <div className="flex items-center gap-2">
        {inlineApps.length > 0 ? (
          <>
            <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-400">Apps</span>
            <div className="flex items-center -space-x-1.5">
              {inlineApps.map((app) => (
                <span
                  key={app.slug}
                  className="relative inline-flex h-7 w-7 items-center justify-center overflow-hidden rounded-lg border-2 border-white bg-indigo-50 shadow-sm"
                  title={app.name}
                >
                  <PipedreamAppIcon app={app} size="sm" />
                </span>
              ))}
            </div>
          </>
        ) : null}
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-indigo-200 hover:text-indigo-600 disabled:cursor-not-allowed disabled:opacity-60"
          aria-label="Manage integrations"
          onClick={openModal}
          disabled={triggerDisabled}
        >
          {settingsQuery.isLoading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <circle cx="5" cy="12" r="1.8"></circle>
              <circle cx="12" cy="12" r="1.8"></circle>
              <circle cx="19" cy="12" r="1.8"></circle>
            </svg>
          )}
        </button>
      </div>
      {modal}
    </>
  )
}
