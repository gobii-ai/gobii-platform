import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Boxes, Check, Loader2, Search, X } from 'lucide-react'

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
  const resultsCountLabel = `${searchResults.length} result${searchResults.length === 1 ? '' : 's'}`

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

  const body = (
    <div className="space-y-6">
      <div className="rounded-3xl border border-slate-200 bg-slate-50 px-5 py-5 text-slate-900">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-indigo-500">Built In</p>
            <p className="mt-2 text-sm leading-relaxed text-slate-600">
              These apps come from the active platform integration configuration.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            {builtinApps.length > 0 ? (
              builtinApps.map((app) => (
                <div key={app.slug} className="inline-flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-3 py-2">
                  <PipedreamAppIcon app={app} />
                  <span className="text-sm font-medium text-slate-900">{app.name}</span>
                </div>
              ))
            ) : (
              <p className="text-sm text-slate-500">No built-in integrations are configured right now.</p>
            )}
          </div>
        </div>
      </div>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">Apps to enable</h3>
            <p className="text-sm text-slate-600">Selected apps will be enabled when you spawn this agent.</p>
          </div>
          <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-700">
            {selectedSlugs.length} selected
          </span>
        </div>
        {selectedApps.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {selectedApps.map((app) => (
              <button
                type="button"
                key={app.slug}
                className="inline-flex items-center gap-2 rounded-full border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 transition hover:border-indigo-300 hover:text-indigo-700"
                onClick={() => toggleSelection(app.slug)}
              >
                <PipedreamAppIcon app={app} size="sm" />
                <span>{app.name}</span>
                <X className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
              </button>
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-sm text-slate-600">
            Search below and pick any additional apps you want enabled for this agent.
          </div>
        )}
      </section>

      <section className="space-y-3">
        <label htmlFor="homepage-integrations-modal-search" className="block text-sm font-medium text-slate-600">
          Search catalog
        </label>
        <label className="relative block text-sm text-slate-500">
          <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
            {searchQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" aria-hidden="true" />}
          </span>
          <input
            id="homepage-integrations-modal-search"
            type="search"
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
            placeholder="Slack, Notion, Salesforce..."
            className="w-full rounded-2xl border border-slate-200 bg-white py-3 pl-10 pr-4 text-base text-slate-900 shadow-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-200"
          />
        </label>
        {searchTerm.trim() ? (
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-slate-500">
              {searchQuery.isError ? 'Search unavailable right now.' : resultsCountLabel}
            </p>
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-2xl border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900"
              onClick={clearSearch}
            >
              Clear
            </button>
          </div>
        ) : null}
      </section>

      {searchTerm.trim().length === 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-5 text-sm text-slate-600">
          Start typing to search more integrations.
        </div>
      ) : searchQuery.isError ? (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-800">
          {resolvePipedreamAppsErrorMessage(searchQuery.error, 'Unable to search integrations right now.')}
        </div>
      ) : searchResults.length > 0 ? (
        <div className="space-y-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-indigo-500">Results</p>
            <h4 className="mt-2 text-xl font-semibold text-slate-900">Matches for &quot;{searchTerm.trim()}&quot;</h4>
          </div>
          <ul className="space-y-3">
            {searchResults.map((app) => {
              const isSelected = selectedSlugs.includes(app.slug)
              const isBuiltin = builtinSlugSet.has(app.slug)
              return (
                <li key={app.slug}>
                  <button
                    type="button"
                    className="flex w-full items-start justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4 text-left transition hover:border-indigo-200 hover:bg-indigo-50/40"
                    onClick={() => toggleSelection(app.slug)}
                    disabled={isBuiltin}
                  >
                    <div className="flex min-w-0 items-start gap-4">
                      <PipedreamAppIcon app={app} />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-base font-semibold text-slate-900">{app.name}</p>
                          <span className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                            {app.slug}
                          </span>
                        </div>
                        {app.description ? (
                          <p className="mt-1 text-sm leading-relaxed text-slate-600">{app.description}</p>
                        ) : (
                          <p className="mt-1 text-sm leading-relaxed text-slate-500">
                            No short description is available for this integration yet.
                          </p>
                        )}
                      </div>
                    </div>
                    <span
                      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${
                        isSelected || isBuiltin
                          ? 'border-indigo-200 bg-indigo-50 text-indigo-700'
                          : 'border-slate-200 text-slate-500'
                      }`}
                    >
                      {isSelected || isBuiltin ? (
                        <>
                          <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                          {isBuiltin ? 'Included' : 'Selected'}
                        </>
                      ) : (
                        'Enable'
                      )}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      ) : !searchQuery.isFetching ? (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-sm text-slate-600">
          No matching integrations found outside the built-in list.
        </div>
      ) : null}
    </div>
  )

  if (isMobile) {
    return (
      <>
        {hiddenFieldsPortal}
        <AgentChatMobileSheet
          open={open}
          onClose={() => setOpen(false)}
          title="Search more integrations"
          subtitle="Built-in apps are ready immediately. Search and enable additional apps for this agent here."
          icon={Search}
          ariaLabel="Search more integrations"
          bodyPadding={false}
        >
          <div className="h-full min-h-0 overflow-y-auto overscroll-contain px-4 pb-6">
            {body}
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
          title="Search more integrations"
          subtitle="Built-in apps are ready immediately. Search and enable additional apps for this agent here."
          onClose={() => setOpen(false)}
          widthClass="sm:max-w-3xl"
          icon={Boxes}
          iconBgClass="bg-indigo-100"
          iconColorClass="text-indigo-600"
          bodyClassName="max-h-[75vh]"
        >
          {body}
        </Modal>
      ) : null}
    </>
  )
}
