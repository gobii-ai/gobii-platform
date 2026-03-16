import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Boxes, Loader2, Search } from 'lucide-react'

import { mapPipedreamApp, searchPipedreamApps } from '../../api/mcp'
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
  searchUrl: string
}

export function HomepageIntegrationsModal({
  builtins,
  initialSearchTerm,
  searchUrl,
}: HomepageIntegrationsModalProps) {
  const [open, setOpen] = useState(Boolean(initialSearchTerm))
  const [isMobile, setIsMobile] = useState(false)
  const [searchTerm, setSearchTerm] = useState(initialSearchTerm)
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState(initialSearchTerm.trim())

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

  const searchQuery = useQuery({
    queryKey: ['homepage-pipedream-app-search', searchUrl, debouncedSearchTerm],
    queryFn: () => searchPipedreamApps(searchUrl, debouncedSearchTerm),
    enabled: debouncedSearchTerm.length > 0,
  })

  const searchResults = searchQuery.data ?? []
  const resultsCountLabel = `${searchResults.length} result${searchResults.length === 1 ? '' : 's'}`

  const clearSearch = () => {
    setSearchTerm('')
    setDebouncedSearchTerm('')
  }

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
          <div className="grid gap-4 md:grid-cols-2">
            {searchResults.map((app) => (
              <div key={app.slug} className="flex items-start gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4">
                <PipedreamAppIcon app={app} />
                <div className="min-w-0">
                  <p className="text-base font-semibold text-slate-900">{app.name}</p>
                  {app.description ? (
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">{app.description}</p>
                  ) : (
                    <p className="mt-1 text-sm leading-relaxed text-slate-500">
                      No short description is available for this integration yet.
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
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
      <AgentChatMobileSheet
        open={open}
        onClose={() => setOpen(false)}
        title="Search more integrations"
        subtitle="Built-in apps are ready immediately. Search the wider integration catalog from here."
        icon={Search}
        ariaLabel="Search more integrations"
        bodyPadding={false}
      >
        <div className="h-full min-h-0 overflow-y-auto overscroll-contain px-4 pb-6">
          {body}
        </div>
      </AgentChatMobileSheet>
    )
  }

  return open ? (
    <Modal
      title="Search more integrations"
      subtitle="Built-in apps are ready immediately. Search the wider integration catalog from here."
      onClose={() => setOpen(false)}
      widthClass="sm:max-w-3xl"
      icon={Boxes}
      iconBgClass="bg-indigo-100"
      iconColorClass="text-indigo-600"
      bodyClassName="max-h-[75vh]"
    >
      {body}
    </Modal>
  ) : null
}
