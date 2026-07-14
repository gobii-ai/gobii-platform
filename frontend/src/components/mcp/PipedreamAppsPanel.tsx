import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchPipedreamAppSettings } from '../../api/mcp'
import { SurfaceHeader, getSettingsSurfaceClassName } from '../common/SettingsSurface'
import {
  PipedreamErrorState,
  PipedreamLoadingState,
} from './PipedreamAppsShared'
import { WorkspaceAppsManager } from './WorkspaceAppsManager'

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
  const queryKey = useMemo(() => ['pipedream-app-settings', settingsUrl] as const, [settingsUrl])
  const settingsQuery = useQuery({
    queryKey,
    queryFn: () => fetchPipedreamAppSettings(settingsUrl as string),
    enabled: Boolean(settingsUrl && searchUrl),
  })
  const emptySettings = useMemo(() => ({
    ownerScope: '',
    ownerLabel: '',
    platformApps: [],
    selectedApps: [],
    effectiveApps: [],
  }), [])
  const hasPipedreamApps = Boolean(settingsUrl && searchUrl)
  const deepLinkRequest = useMemo(() => {
    if (typeof window === 'undefined') {
      return null
    }
    const params = new URLSearchParams(window.location.search)
    return {
      providerKey: params.get('provider')?.trim() || null,
      connect: params.get('connect') === '1',
    }
  }, [])
  const sectionClassName = embedded
    ? getSettingsSurfaceClassName({ variant: 'embedded', roundedClassName: 'rounded-xl' })
    : 'gobii-card-base overflow-hidden'
  const contentClassName = embedded ? 'px-6 pb-6' : 'px-6 py-5'

  return (
    <section className={sectionClassName}>
      <SurfaceHeader
        variant={embedded ? 'embedded' : 'standalone'}
        title="Apps"
        subtitle="Search apps and manage agent connections."
        titleClassName={embedded ? 'text-2xl font-semibold text-slate-50' : undefined}
      />

      <div className={contentClassName}>
        {hasPipedreamApps && settingsQuery.isLoading ? (
          <PipedreamLoadingState label="Loading apps…" surface={embedded ? 'embedded' : 'standalone'} />
        ) : hasPipedreamApps && settingsQuery.isError ? (
          <PipedreamErrorState
            error={settingsQuery.error}
            fallback="Unable to load apps right now."
            surface={embedded ? 'embedded' : 'standalone'}
          />
        ) : (
          <WorkspaceAppsManager
            settingsUrl={settingsUrl ?? null}
            searchUrl={searchUrl ?? null}
            nativeIntegrationsUrl={nativeIntegrationsUrl}
            initialNativeProviderKey={deepLinkRequest?.providerKey ?? null}
            initialNativeConnect={Boolean(deepLinkRequest?.connect)}
            initialSettings={settingsQuery.data ?? emptySettings}
            onError={onError}
            surface={embedded ? 'embedded' : 'standalone'}
          />
        )}
      </div>
    </section>
  )
}
