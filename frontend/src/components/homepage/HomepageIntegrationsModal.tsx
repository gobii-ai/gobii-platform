import { Fragment, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Check, Loader2, Search, Sparkles, X } from 'lucide-react'

import { scheduleLoginRedirect } from '../../api/http'
import { mapPipedreamApp, searchPipedreamApps, type PipedreamAppSummary } from '../../api/mcp'
import {
  fetchNativeIntegrations,
  mapNativeIntegrationProvider,
  revokeNativeIntegration,
  startNativeIntegrationConnect,
  type NativeIntegrationProvider,
  type NativeIntegrationProviderDTO,
} from '../../api/nativeIntegrations'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { useIsMobile } from '../../hooks/useIsMobile'
import { ImmersiveDialog } from '../common/ImmersiveDialog'
import {
  confirmNativeIntegrationDisconnect,
  NativeIntegrationActionButtons,
  NativeIntegrationFilesDisclosure,
  NativeIntegrationSummaryCell,
  openGoogleDrivePicker,
  openNativeOAuthPopup,
  usesManualNativeIntegrationCredentials,
  useNativeIntegrationConnectMutation,
  useNativeIntegrationDisconnectMutation,
  useNativeIntegrationPickerMutation,
  useNativeIntegrationRefreshEffects,
} from '../mcp/NativeIntegrationShared'
import { useManualNativeIntegrationConnect } from '../mcp/useManualNativeIntegrationConnect'
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
  nativeIntegrationsUrl: string
  nativeProviders: NativeIntegrationProviderDTO[]
  isAuthenticated: boolean
  searchUrl: string
  selectedFieldsContainerId: string
  initialOpen?: boolean
  openNativePicker?: typeof openGoogleDrivePicker
}

function fallbackAppForSlug(slug: string): PipedreamAppSummary {
  return {
    slug,
    name: slug.replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase()),
    description: '',
    iconUrl: '',
  }
}

function scrollPageToTop() {
  window.scrollTo(0, 0)
  document.documentElement.scrollTop = 0
  document.body.scrollTop = 0
}

async function ensureHomepageCsrf(): Promise<string> {
  const response = await fetch('/api/homepage/csrf-token/', {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error('Unable to refresh the CSRF token.')
  }
  const payload = await response.json() as { csrfToken?: unknown } | null
  return typeof payload?.csrfToken === 'string' ? payload.csrfToken : ''
}

export function buildHomepageNativeIntegrationLoginReturnUrl(
  provider: Pick<NativeIntegrationProvider, 'displayName' | 'providerKey'>,
  currentHref = typeof window === 'undefined' ? '/' : window.location.href,
): string {
  const url = new URL(currentHref, typeof window === 'undefined' ? 'http://localhost' : window.location.origin)
  url.searchParams.set('integration_search', provider.displayName || provider.providerKey)
  return `${url.pathname}${url.search}${url.hash}`
}

export function HomepageIntegrationsModal({
  builtins,
  initialSearchTerm,
  initialSelectedAppSlugs,
  nativeIntegrationsUrl,
  nativeProviders,
  isAuthenticated,
  searchUrl,
  selectedFieldsContainerId,
  initialOpen = false,
  openNativePicker = openGoogleDrivePicker,
}: HomepageIntegrationsModalProps) {
  const [open, setOpen] = useState(Boolean(initialOpen || initialSearchTerm))
  const isMobile = useIsMobile()
  const [searchTerm, setSearchTerm] = useState(initialSearchTerm)
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState(initialSearchTerm.trim())
  const [selectedSlugs, setSelectedSlugs] = useState<string[]>(() => initialSelectedAppSlugs)
  const [pendingNativeAction, setPendingNativeAction] = useState<{
    providerKey: string
    kind: 'connect' | 'disconnect' | 'picker'
  } | null>(null)
  const [nativeErrorMessage, setNativeErrorMessage] = useState<string | null>(null)
  const nativeQueryKey = useMemo(
    () => ['native-integrations', nativeIntegrationsUrl] as const,
    [nativeIntegrationsUrl],
  )
  useNativeIntegrationRefreshEffects({
    queryKey: nativeQueryKey,
    onError: setNativeErrorMessage,
  })
  const [knownApps, setKnownApps] = useState<Record<string, PipedreamAppSummary>>(() => {
    const builtinApps = builtins.map(mapPipedreamApp)
    return Object.fromEntries(builtinApps.map((app) => [app.slug, app]))
  })
  const seededNativeProviders = useMemo(
    () => nativeProviders.map(mapNativeIntegrationProvider),
    [nativeProviders],
  )

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
    document.addEventListener('homepage-integrations:open', openModal)
    openButtons.forEach((button) => {
      button.addEventListener('click', openModal)
    })
    return () => {
      document.removeEventListener('homepage-integrations:open', openModal)
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
  const nativeIntegrationsQuery = useQuery({
    queryKey: nativeQueryKey,
    queryFn: () => fetchNativeIntegrations(nativeIntegrationsUrl),
    enabled: isAuthenticated && nativeIntegrationsUrl.length > 0,
  })

  const searchResults = searchQuery.data ?? []
  const currentNativeProviders = nativeIntegrationsQuery.data?.providers ?? seededNativeProviders
  const visibleNativeProviders = useMemo(() => {
    const normalizedSearch = debouncedSearchTerm.toLowerCase()
    if (!normalizedSearch) {
      return currentNativeProviders
    }
    return currentNativeProviders.filter((provider) => [
      provider.providerKey,
      provider.displayName,
      provider.description,
    ].some((value) => value.toLowerCase().includes(normalizedSearch)))
  }, [currentNativeProviders, debouncedSearchTerm])

  const nativeConnectMutation = useNativeIntegrationConnectMutation({
    setPendingAction: setPendingNativeAction,
    setStatusMessage: setNativeErrorMessage,
    startConnect: async (provider) => {
      const csrfToken = await ensureHomepageCsrf()
      return startNativeIntegrationConnect(provider.connectUrl, csrfToken)
    },
  })

  const nativeDisconnectMutation = useNativeIntegrationDisconnectMutation({
    nativeQueryKey,
    setPendingAction: setPendingNativeAction,
    setStatusMessage: setNativeErrorMessage,
    disconnect: async (provider) => {
      const csrfToken = await ensureHomepageCsrf()
      return revokeNativeIntegration(provider.revokeUrl, csrfToken).then(() => provider)
    },
  })

  const {
    credentialModal,
    isPending: manualNativeConnectPending,
    openCredentialModal,
  } = useManualNativeIntegrationConnect({
    nativeQueryKey,
    getCsrfToken: ensureHomepageCsrf,
    onMutate: (provider) => {
      setPendingNativeAction({ providerKey: provider.providerKey, kind: 'connect' })
      setNativeErrorMessage(null)
    },
    onSuccess: (payload) => {
      setNativeErrorMessage(payload.connected ? null : 'Saved credentials. Add the remaining required fields to finish setup.')
    },
    onError: setNativeErrorMessage,
    onSettled: () => setPendingNativeAction(null),
  })

  const nativePickerMutation = useNativeIntegrationPickerMutation({
    setPendingAction: setPendingNativeAction,
    setStatusMessage: setNativeErrorMessage,
    openPicker: openNativePicker,
    preparePicker: () => {
      const previousScrollX = window.scrollX
      const previousScrollY = window.scrollY
      scrollPageToTop()
      return () => {
        window.scrollTo(previousScrollX, previousScrollY)
      }
    },
  })

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
            <h3 className="text-sm font-semibold text-slate-900">Native apps</h3>
            <p className="text-sm text-slate-600">Connected credentials are shared across your workspace.</p>
          </div>
        </div>
        {nativeErrorMessage ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {nativeErrorMessage}
          </div>
        ) : null}
        {nativeIntegrationsQuery.isLoading ? (
          <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-4 text-sm text-slate-600">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading native apps…
          </div>
        ) : nativeIntegrationsQuery.isError ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {safeErrorMessage(nativeIntegrationsQuery.error)}
          </div>
        ) : visibleNativeProviders.length > 0 ? (
          <div className="space-y-2">
            {visibleNativeProviders.map((provider) => (
              <HomepageNativeProviderRow
                key={provider.providerKey}
                provider={provider}
                pendingAction={pendingNativeAction}
                disabled={nativeConnectMutation.isPending || manualNativeConnectPending || nativeDisconnectMutation.isPending || nativePickerMutation.isPending}
                onConnect={() => {
                  if (!isAuthenticated) {
                    scheduleLoginRedirect(buildHomepageNativeIntegrationLoginReturnUrl(provider))
                    return
                  }
                  if (usesManualNativeIntegrationCredentials(provider)) {
                    openCredentialModal(provider)
                    return
                  }
                  nativeConnectMutation.mutate({ provider, popup: openNativeOAuthPopup(provider) })
                }}
                onDisconnect={() => {
                  if (confirmNativeIntegrationDisconnect(provider)) {
                    nativeDisconnectMutation.mutate(provider)
                  }
                }}
                onPicker={() => nativePickerMutation.mutate(provider)}
              />
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 text-sm text-slate-600">
            No native apps matched your search.
          </div>
        )}
      </section>

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

  return (
    <>
      {hiddenFieldsPortal}
      <ImmersiveDialog
        open={open}
        title="Manage integrations"
        subtitle="Search available apps and enable additional ones."
        onClose={() => setOpen(false)}
        footer={actions}
        desktopWidthClass="sm:max-w-4xl"
        icon={Sparkles}
        ariaLabel="Manage integrations"
        bodyPadding={false}
        desktopIconBgClass="bg-blue-100"
        desktopIconColorClass="text-blue-700"
        dismissible={!nativePickerMutation.isPending}
        mobileChildren={(
          <div className="h-full min-h-0 overflow-y-auto overscroll-contain px-4 pb-6">
            <div className="pt-4">
              {body}
            </div>
            <div className="flex flex-col gap-3 pb-2 pt-5">
              {actions}
            </div>
          </div>
        )}
      >
        {body}
      </ImmersiveDialog>
      {credentialModal}
    </>
  )
}

function HomepageNativeProviderRow({
  provider,
  pendingAction,
  disabled,
  onConnect,
  onDisconnect,
  onPicker,
}: {
  provider: NativeIntegrationProvider
  pendingAction: { providerKey: string; kind: 'connect' | 'disconnect' | 'picker' } | null
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  onPicker: () => void
}) {
  const isPending = pendingAction?.providerKey === provider.providerKey
  const pendingKind = isPending ? pendingAction?.kind : null

  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex min-w-72 flex-1 items-start gap-3">
          <NativeIntegrationSummaryCell
            provider={provider}
            descriptionClassName="mt-1 text-sm text-slate-600"
            badge="native-connected"
          />
        </div>
        <div className="ml-auto flex shrink-0 flex-wrap justify-end gap-2">
          <NativeIntegrationActionButtons
            provider={provider}
            pendingKind={pendingKind}
            disabled={disabled}
            onConnect={onConnect}
            onDisconnect={onDisconnect}
            onPicker={onPicker}
            disconnectTone="neutral"
            minWidth={false}
          />
        </div>
      </div>
      <NativeIntegrationFilesDisclosure provider={provider} />
    </div>
  )
}
