import { Fragment, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Check, Loader2, Search, Sparkles, X } from 'lucide-react'

import { mapPipedreamApp, searchPipedreamApps, type PipedreamAppSummary } from '../../api/mcp'
import { AgentChatMobileSheet } from '../agentChat/AgentChatMobileSheet'
import { Modal } from '../common/Modal'
import { PipedreamAppIcon, resolvePipedreamAppsErrorMessage } from '../mcp/PipedreamAppsShared'

type HomepageIntegrationsModalAppDTO = {
  slug: string
  name: string
  description: string
  icon_url: string
}

export type HomepageIntegrationsModalProps = {
  builtins: HomepageIntegrationsModalAppDTO[]
  initialSearchTerm: string
  initialSelectedAppSlugs: string[]
  searchUrl: string
  selectedFieldsContainerId: string
}

function fallbackAppForSlug(slug: string): PipedreamAppSummary {
  return {
    slug,
    name: slug.replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase()),
    description: '',
    iconUrl: '',
  }
}

export function HomepageIntegrationsModal({
  builtins,
  initialSearchTerm,
  initialSelectedAppSlugs,
  searchUrl,
  selectedFieldsContainerId,
}: HomepageIntegrationsModalProps) {
  const [open, setOpen] = useState(Boolean(initialSearchTerm))
  const [isMobile, setIsMobile] = useState(false)
  const [searchTerm, setSearchTerm] = useState(initialSearchTerm)
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState(initialSearchTerm.trim())
  const [selectedSlugs, setSelectedSlugs] = useState<string[]>(() => initialSelectedAppSlugs)
  const [knownApps, setKnownApps] = useState<Record<string, PipedreamAppSummary>>(() => {
    const builtinApps = builtins.map(mapPipedreamApp)
    return Object.fromEntries(builtinApps.map((app) => [app.slug, app]))
  })

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setDebouncedSearchTerm(searchTerm.trim())
    }, 250)
    return () => window.clearTimeout(timeoutId)
  }, [searchTerm])

  useEffect(() => {
    const openButtons = Array.from(document.querySelectorAll<HTMLElement>('[data-integrations-open]'))
    if (openButtons.length === 0) {
      return
    }
    const openModal = () => setOpen(true)
    openButtons.forEach((button) => {
      button.addEventListener('click', openModal)
    })
    return () => {
      openButtons.forEach((button) => {
        button.removeEventListener('click', openModal)
      })
    }
  }, [])

  const builtinApps = useMemo(() => builtins.map(mapPipedreamApp), [builtins])
  const builtinSlugSet = useMemo(() => new Set(builtinApps.map((app) => app.slug)), [builtinApps])

  const searchQuery = useQuery({
    queryKey: ['homepage-pipedream-app-search', searchUrl, debouncedSearchTerm],
    queryFn: () => searchPipedreamApps(searchUrl, debouncedSearchTerm),
    enabled: debouncedSearchTerm.length > 0,
  })

  const searchResults = searchQuery.data ?? []

  useEffect(() => {
    const nextEntries = [...builtinApps, ...searchResults]
    if (nextEntries.length === 0) {
      return
    }
    setKnownApps((current) => {
      const next = { ...current }
      let changed = false
      nextEntries.forEach((app) => {
        if (!next[app.slug]) {
          next[app.slug] = app
          changed = true
        }
      })
      return changed ? next : current
    })
  }, [builtinApps, searchResults])

  const selectedApps = useMemo(
    () => selectedSlugs.map((slug) => knownApps[slug] ?? fallbackAppForSlug(slug)),
    [knownApps, selectedSlugs],
  )

  const clearSearch = () => {
    setSearchTerm('')
    setDebouncedSearchTerm('')
  }

  const toggleSelection = (slug: string) => {
    if (builtinSlugSet.has(slug)) {
      return
    }
    setSelectedSlugs((current) => {
      if (current.includes(slug)) {
        return current.filter((item) => item !== slug)
      }
      return [...current, slug]
    })
  }

  const hiddenFieldsContainer =
    typeof document === 'undefined' ? null : document.getElementById(selectedFieldsContainerId)

  const hiddenFieldsPortal = hiddenFieldsContainer
    ? createPortal(
        <>
          {selectedSlugs.map((slug) => (
            <input key={slug} type="hidden" name="selected_pipedream_app_slugs" value={slug} />
          ))}
        </>,
        hiddenFieldsContainer,
      )
    : null

  const actions = (
    <Fragment>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm"
        onClick={() => setOpen(false)}
      >
        Done
      </button>
    </Fragment>
  )

  const body = (
    <div className="space-y-5 p-1">
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">Built-in apps</h3>
            <p className="text-sm text-slate-600">These apps are included automatically for this agent.</p>
          </div>
        </div>
        {builtinApps.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {builtinApps.map((app) => (
              <span
                key={app.slug}
                className="inline-flex items-center gap-2 rounded-full border border-blue-200 bg-white px-3 py-2 text-sm font-medium text-slate-800"
              >
                <PipedreamAppIcon app={app} size="sm" />
                <span>{app.name}</span>
              </span>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-slate-200 bg-white px-4 py-4 text-sm text-slate-600">
            No built-in apps configured.
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">Added apps</h3>
            <p className="text-sm text-slate-600">Selected apps will be enabled when you spawn this agent.</p>
          </div>
          <span className="rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">
            {selectedSlugs.length} selected
          </span>
        </div>
        {selectedApps.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {selectedApps.map((app) => (
              <button
                type="button"
                key={app.slug}
                className="inline-flex items-center gap-2 rounded-full border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 transition hover:border-blue-300 hover:text-blue-700"
                onClick={() => toggleSelection(app.slug)}
              >
                <PipedreamAppIcon app={app} />
                <span>{app.name}</span>
                <X className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
              </button>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-slate-200 bg-white px-4 py-4 text-sm text-slate-600">
            No additional apps enabled yet.
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <label htmlFor="homepage-integrations-modal-search" className="text-sm font-semibold text-slate-900">
            Search apps
          </label>
          {searchTerm.trim() ? (
            <button
              type="button"
              className="text-sm font-medium text-slate-500 transition hover:text-slate-700"
              onClick={clearSearch}
            >
              Clear
            </button>
          ) : null}
        </div>
        <label className="relative block text-sm text-slate-500">
          <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
            {searchQuery.isFetching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" aria-hidden="true" />
            )}
          </span>
          <input
            id="homepage-integrations-modal-search"
            type="search"
            className="w-full rounded-lg border border-slate-300 py-3 pl-10 pr-3 text-sm text-slate-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-blue-500"
            placeholder="Search apps"
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
          />
        </label>

        {searchTerm.trim().length === 0 ? (
          <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 text-sm text-slate-600">
            Start typing to search available apps.
          </div>
        ) : searchQuery.isError ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {resolvePipedreamAppsErrorMessage(searchQuery.error, 'Unable to search apps.')}
          </div>
        ) : searchResults.length === 0 && !searchQuery.isFetching ? (
          <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 text-sm text-slate-600">
            No apps matched your search.
          </div>
        ) : (
          <ul className={`overflow-y-auto rounded-lg border border-slate-200 ${isMobile ? 'bg-white' : 'max-h-96'}`}>
            {searchResults.map((app) => {
              const isSelected = selectedSlugs.includes(app.slug)
              const isBuiltin = builtinSlugSet.has(app.slug)
              return (
                <li key={app.slug} className="border-b border-slate-200 last:border-b-0">
                  <button
                    type="button"
                    className="flex w-full items-start justify-between gap-4 px-4 py-3 text-left transition hover:bg-slate-50"
                    onClick={() => toggleSelection(app.slug)}
                    disabled={isBuiltin}
                  >
                    <div className="flex min-w-0 items-start gap-3">
                      <PipedreamAppIcon app={app} />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-semibold text-slate-900">{app.name}</p>
                          <span className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                            {app.slug}
                          </span>
                          {isBuiltin ? (
                            <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                              Included
                            </span>
                          ) : null}
                        </div>
                        {app.description ? <p className="mt-1 text-sm text-slate-600">{app.description}</p> : null}
                      </div>
                    </div>
                    <span
                      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${
                        isSelected || isBuiltin
                          ? 'border-blue-200 bg-blue-50 text-blue-700'
                          : 'border-slate-200 text-slate-500'
                      }`}
                    >
                      {isSelected || isBuiltin ? (
                        <>
                          <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                          {isBuiltin ? 'Included' : 'Selected'}
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
  )

  if (isMobile) {
    return (
      <>
        {hiddenFieldsPortal}
        <AgentChatMobileSheet
          open={open}
          onClose={() => setOpen(false)}
          title="Manage integrations"
          subtitle="Search available apps and enable additional ones."
          icon={Sparkles}
          ariaLabel="Manage integrations"
          bodyPadding={false}
        >
          <div className="h-full min-h-0 overflow-y-auto overscroll-contain px-4 pb-6">
            <div className="pt-4">
              {body}
            </div>
            <div className="flex flex-col gap-3 pb-2 pt-5">
              {actions}
            </div>
          </div>
        </AgentChatMobileSheet>
      </>
    )
  }

  return (
    <>
      {hiddenFieldsPortal}
      {open ? (
        <Modal
          title="Manage integrations"
          subtitle="Search available apps and enable additional ones."
          onClose={() => setOpen(false)}
          footer={actions}
          widthClass="sm:max-w-4xl"
          icon={Sparkles}
          iconBgClass="bg-blue-100"
          iconColorClass="text-blue-700"
        >
          {body}
        </Modal>
      ) : null}
    </>
  )
}
