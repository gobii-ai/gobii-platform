import { useMemo } from 'react'

import { SettingsSurfaceProvider, SurfaceHeader, getSettingsSurfaceClassName } from '../common/SettingsSurface'
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
  const surface = embedded ? 'embedded' : 'standalone'

  return (
    <SettingsSurfaceProvider variant={surface}>
      <section className={sectionClassName}>
        <SurfaceHeader
          variant={surface}
          title="Apps"
          subtitle="Search apps and manage agent connections."
          titleClassName={embedded ? 'text-2xl font-semibold text-slate-50' : undefined}
        />

        <div className={contentClassName}>
          <WorkspaceAppsManager
            settingsUrl={settingsUrl ?? null}
            searchUrl={searchUrl ?? null}
            nativeIntegrationsUrl={nativeIntegrationsUrl}
            initialNativeProviderKey={deepLinkRequest?.providerKey ?? null}
            initialNativeConnect={Boolean(deepLinkRequest?.connect)}
            onError={onError}
          />
        </div>
      </section>
    </SettingsSurfaceProvider>
  )
}
