import { Fragment, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Loader2, Search, Sparkles, X } from 'lucide-react'

import {
  searchPipedreamApps,
  updatePipedreamAppSettings,
  type PipedreamAppSettings,
} from '../../api/mcp'
import { Modal } from '../common/Modal'
import { PipedreamAppIcon, resolvePipedreamAppsErrorMessage } from './PipedreamAppsShared'

type PipedreamAppsModalProps = {
  settingsUrl: string
  searchUrl: string
  initialSettings: PipedreamAppSettings
  onClose: () => void
  onSuccess: (message: string) => void
  onError: (message: string) => void
}

export function PipedreamAppsModal({
  settingsUrl,
  searchUrl,
  initialSettings,
  onClose,
  onSuccess,
  onError,
}: PipedreamAppsModalProps) {
  const queryClient = useQueryClient()
  const settingsQueryKey = useMemo(() => ['pipedream-app-settings', settingsUrl] as const, [settingsUrl])
  const [searchTerm, setSearchTerm] = useState('')
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState('')
  const [selectedSlugs, setSelectedSlugs] = useState<string[]>(
    () => initialSettings.selectedApps.map((app) => app.slug),
  )
  const [statusMessage, setStatusMessage] = useState<string | null>(null)

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setDebouncedSearchTerm(searchTerm.trim())
    }, 250)
    return () => window.clearTimeout(timeoutId)
  }, [searchTerm])

  const searchQuery = useQuery({
    queryKey: ['pipedream-app-search', searchUrl, debouncedSearchTerm],
    queryFn: () => searchPipedreamApps(searchUrl, debouncedSearchTerm),
    enabled: debouncedSearchTerm.length > 0,
  })

  const mutation = useMutation({
    mutationFn: (nextSelectedSlugs: string[]) => updatePipedreamAppSettings(settingsUrl, nextSelectedSlugs),
    onSuccess: (updatedSettings) => {
      queryClient.setQueryData(settingsQueryKey, updatedSettings)
      const message = updatedSettings.message ?? 'Apps updated.'
      onSuccess(message)
      onClose()
    },
    onError: (error) => {
      const message = resolvePipedreamAppsErrorMessage(error, 'Unable to update apps.')
      setStatusMessage(message)
      onError(message)
    },
  })

  const platformSlugSet = useMemo(
    () => new Set(initialSettings.platformApps.map((app) => app.slug)),
    [initialSettings.platformApps],
  )

  const selectedAppMap = useMemo(() => {
    const entries = initialSettings.selectedApps.map((app) => [app.slug, app] as const)
    return new Map(entries)
  }, [initialSettings.selectedApps])

  const searchResults = searchQuery.data ?? []
  const visibleSelectedApps = useMemo(() => {
    const fallbackApps = selectedSlugs.map((slug) => {
      const known = selectedAppMap.get(slug)
      if (known) {
        return known
      }
      return {
        slug,
        name: slug.replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase()),
        description: '',
        iconUrl: '',
      }
    })
    return fallbackApps
  }, [selectedAppMap, selectedSlugs])

  const handleToggle = (slug: string) => {
    setSelectedSlugs((current) => {
      if (platformSlugSet.has(slug)) {
        return current
      }
      if (current.includes(slug)) {
        return current.filter((item) => item !== slug)
      }
      return [...current, slug]
    })
  }

  const handleRemove = (slug: string) => {
    if (platformSlugSet.has(slug)) {
      return
    }
    setSelectedSlugs((current) => current.filter((item) => item !== slug))
  }

  const footer = (
    <Fragment>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        onClick={() => mutation.mutate(selectedSlugs)}
        disabled={mutation.isPending}
      >
        {mutation.isPending ? 'Saving…' : 'Save Apps'}
      </button>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 sm:w-auto sm:text-sm"
        onClick={onClose}
        disabled={mutation.isPending}
      >
        Cancel
      </button>
    </Fragment>
  )

  return (
    <Modal
      title="Add Apps"
      subtitle={`Search available apps and enable additional ones for ${initialSettings.ownerLabel}.`}
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-4xl"
      icon={Sparkles}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-700"
    >
      <div className="space-y-5 p-1">
        {statusMessage && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {statusMessage}
          </div>
        )}

        <section className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">Added apps</h3>
              <p className="text-xs text-slate-500">Included apps stay on automatically. Only the ones you added can be removed here.</p>
            </div>
            <span className="rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">
              {selectedSlugs.length} selected
            </span>
          </div>
          {visibleSelectedApps.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {visibleSelectedApps.map((app) => (
                <button
                  type="button"
                  key={app.slug}
                  className="inline-flex items-center gap-2 rounded-full border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 transition hover:border-blue-300 hover:text-blue-700"
                  onClick={() => handleRemove(app.slug)}
                  disabled={mutation.isPending}
                >
                  <PipedreamAppIcon app={app} />
                  <span>{app.name}</span>
                  <X className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
                </button>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50/60 px-4 py-4 text-sm text-slate-600">
              No additional apps enabled yet.
            </div>
          )}
        </section>

        <section className="space-y-3">
          <label className="relative block text-sm text-slate-500">
            <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
              {searchQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" aria-hidden="true" />}
            </span>
            <input
              type="search"
              className="w-full rounded-lg border border-slate-300 py-3 pl-10 pr-3 text-sm text-slate-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-blue-500"
              placeholder="Search apps"
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              disabled={mutation.isPending}
            />
          </label>

          {searchTerm.trim().length === 0 ? (
            <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-4 py-4 text-sm text-slate-600">
              Start typing to search available apps.
            </div>
          ) : searchQuery.isError ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {resolvePipedreamAppsErrorMessage(searchQuery.error, 'Unable to search apps.')}
            </div>
          ) : searchResults.length === 0 && !searchQuery.isFetching ? (
            <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-4 py-4 text-sm text-slate-600">
              No apps matched your search.
            </div>
          ) : (
            <ul className="max-h-96 overflow-y-auto rounded-lg border border-slate-200">
              {searchResults.map((app) => {
                const isSelected = selectedSlugs.includes(app.slug)
                const isPlatform = platformSlugSet.has(app.slug)
                return (
                  <li key={app.slug} className="border-b border-slate-200 last:border-b-0">
                    <button
                      type="button"
                      className="flex w-full items-start justify-between gap-4 px-4 py-3 text-left transition hover:bg-slate-50"
                      onClick={() => handleToggle(app.slug)}
                      disabled={mutation.isPending || isPlatform}
                    >
                      <div className="flex min-w-0 items-start gap-3">
                        <PipedreamAppIcon app={app} />
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-semibold text-slate-900">{app.name}</p>
                            <span className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                              {app.slug}
                            </span>
                            {isPlatform && (
                              <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                                Included
                              </span>
                            )}
                          </div>
                          {app.description && <p className="mt-1 text-sm text-slate-600">{app.description}</p>}
                        </div>
                      </div>
                      <span
                        className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${
                          isSelected || isPlatform
                            ? 'border-blue-200 bg-blue-50 text-blue-700'
                            : 'border-slate-200 text-slate-500'
                        }`}
                      >
                        {isSelected || isPlatform ? (
                          <>
                            <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                            {isPlatform ? 'Included' : 'Selected'}
                          </>
                        ) : (
                          'Select'
                        )}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </div>
    </Modal>
  )
}
