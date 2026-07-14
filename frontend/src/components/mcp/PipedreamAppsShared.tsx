import { useEffect, useState, type ReactNode } from 'react'
import { Loader2, Plug, Search, Sparkles, Trash2, Unplug } from 'lucide-react'

import { HttpError } from '../../api/http'
import type { PipedreamAppAgentConnection, PipedreamAppSummary } from '../../api/mcp'
import { ImmersiveDialog } from '../common/ImmersiveDialog'
import type { SettingsSurfaceVariant } from '../common/SettingsSurface'
export { useIsMobile } from '../../hooks/useIsMobile'

export type PipedreamStatusMessage = {
  text: string
  tone?: 'error'
} | null

type PipedreamAppIconProps = {
  app: PipedreamAppSummary
  size?: 'sm' | 'md'
  surface?: SettingsSurfaceVariant
}

export function PipedreamAppIcon({ app, size = 'md', surface = 'standalone' }: PipedreamAppIconProps) {
  const sizeClass = size === 'sm' ? 'h-6 w-6 rounded-lg text-[10px]' : 'h-9 w-9 rounded-lg text-xs'

  if (app.iconUrl) {
    return (
      <img
        src={app.iconUrl}
        alt=""
        className={`${sizeClass} border border-slate-200 bg-white object-cover`}
        loading="lazy"
      />
    )
  }

  const fallbackClassName = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-900 text-slate-200'
    : 'border-slate-200 bg-white text-slate-700'

  return (
    <span className={`inline-flex items-center justify-center border font-semibold uppercase ${fallbackClassName} ${sizeClass}`}>
      {app.name.slice(0, 2)}
    </span>
  )
}

export function useDebouncedValue(value: string, delayMs = 250): string {
  const [debouncedValue, setDebouncedValue] = useState(value)

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebouncedValue(value.trim()), delayMs)
    return () => window.clearTimeout(timeoutId)
  }, [delayMs, value])

  return debouncedValue
}

export function useWindowFocusRefetch(refetch: () => unknown, enabled = true): void {
  useEffect(() => {
    if (!enabled) {
      return
    }
    const handleFocus = () => refetch()
    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [enabled, refetch])
}

export function PipedreamModalShell({
  title,
  subtitle,
  ariaLabel,
  onClose,
  children,
}: {
  title: string
  subtitle: string
  ariaLabel?: string
  onClose: () => void
  children: ReactNode
}) {
  return (
    <ImmersiveDialog
      open
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      icon={Sparkles}
      ariaLabel={ariaLabel ?? title}
      bodyPadding={false}
      desktopWidthClass="sm:max-w-5xl"
      desktopIconBgClass="bg-blue-100"
      desktopIconColorClass="text-blue-700"
      mobileChildren={(
        <div className="h-full min-h-0 overflow-y-auto overscroll-contain px-4 pb-6 pt-4">
          {children}
        </div>
      )}
    >
      {children}
    </ImmersiveDialog>
  )
}

export function PipedreamStatusBanner({
  statusMessage,
  surface = 'standalone',
}: {
  statusMessage: PipedreamStatusMessage
  surface?: SettingsSurfaceVariant
}) {
  if (!statusMessage) {
    return null
  }
  const isError = statusMessage.tone === 'error'
  const className = surface === 'embedded'
    ? isError
      ? 'border-rose-300/25 bg-rose-950/35 text-rose-200'
      : 'border-emerald-300/25 bg-emerald-950/35 text-emerald-200'
    : isError
      ? 'border-red-200 bg-red-50 text-red-700'
      : 'border-emerald-200 bg-emerald-50 text-emerald-700'
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${className}`}>
      {statusMessage.text}
    </div>
  )
}

export function PipedreamSearchInput({
  value,
  onChange,
  isFetching,
  disabled,
  surface = 'standalone',
}: {
  value: string
  onChange: (value: string) => void
  isFetching: boolean
  disabled: boolean
  surface?: SettingsSurfaceVariant
}) {
  const inputClassName = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/45 text-slate-100 placeholder:text-slate-500 focus:border-sky-300/50 focus:ring-sky-300/30'
    : 'border-slate-300 bg-white text-slate-800 shadow-sm focus:border-blue-500 focus:ring-blue-500'
  const labelClassName = surface === 'embedded' ? 'text-slate-400' : 'text-slate-500'
  return (
    <label className={`relative block text-sm ${labelClassName}`}>
      <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
        {isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" aria-hidden="true" />}
      </span>
      <input
        type="search"
        className={`w-full rounded-lg border py-3 pl-10 pr-3 text-sm focus:outline-none focus:ring-1 ${inputClassName}`}
        placeholder="Search apps"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      />
    </label>
  )
}

export function PipedreamListFrame({
  isMobile,
  constrainHeight = true,
  surface = 'standalone',
  children,
}: {
  isMobile: boolean
  constrainHeight?: boolean
  surface?: SettingsSurfaceVariant
  children: ReactNode
}) {
  const frameClassName = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-900'
    : 'border-slate-200 bg-white'
  const dividerClassName = surface === 'embedded' ? 'divide-slate-200/10' : 'divide-slate-200'
  return (
    <div className={`overflow-hidden rounded-lg border ${frameClassName} ${!isMobile && constrainHeight ? 'max-h-[28rem] overflow-y-auto' : ''}`}>
      <div className={`divide-y ${dividerClassName}`}>
        {children}
      </div>
    </div>
  )
}

export function PipedreamLoadingState({ label, surface = 'standalone' }: { label: string; surface?: SettingsSurfaceVariant }) {
  const className = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/30 text-slate-400'
    : 'border-slate-200 bg-white text-slate-600'
  return (
    <div className={`flex items-center gap-2 rounded-lg border px-4 py-5 text-sm ${className}`}>
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </div>
  )
}

export function PipedreamEmptyState({ label, surface = 'standalone' }: { label: string; surface?: SettingsSurfaceVariant }) {
  const className = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/30 text-slate-400'
    : 'border-slate-200 bg-white text-slate-600'
  return (
    <div className={`rounded-lg border px-4 py-5 text-sm ${className}`}>
      {label}
    </div>
  )
}

export function PipedreamErrorState({
  error,
  fallback,
  surface = 'standalone',
}: {
  error: unknown
  fallback: string
  surface?: SettingsSurfaceVariant
}) {
  const className = surface === 'embedded'
    ? 'border-rose-300/25 bg-rose-950/35 text-rose-200'
    : 'border-red-200 bg-red-50 text-red-700'
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${className}`}>
      {resolvePipedreamAppsErrorMessage(error, fallback)}
    </div>
  )
}

export function PipedreamAppSummaryCell({
  app,
  surface = 'standalone',
}: {
  app: PipedreamAppSummary
  surface?: SettingsSurfaceVariant
}) {
  const titleClassName = surface === 'embedded' ? 'text-slate-100' : 'text-slate-900'
  const descriptionClassName = surface === 'embedded' ? 'text-slate-400' : 'text-slate-600'
  return (
    <div className="flex min-w-0 items-center gap-3">
      <PipedreamAppIcon app={app} surface={surface} />
      <div className="min-w-0">
        <p className={`truncate text-sm font-semibold ${titleClassName}`}>{app.name}</p>
        {app.description ? <p className={`mt-1 line-clamp-2 text-sm ${descriptionClassName}`}>{app.description}</p> : null}
      </div>
    </div>
  )
}

export function PipedreamConnectionButton({
  connected,
  pendingKind,
  disabled,
  onConnect,
  onDisconnect,
  surface = 'standalone',
}: {
  connected: boolean
  pendingKind: 'connect' | 'disconnect' | null
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
  surface?: SettingsSurfaceVariant
}) {
  if (connected) {
    const disconnectClassName = surface === 'embedded'
      ? 'border-rose-300/25 bg-rose-950/20 text-rose-200 hover:border-rose-200/40 hover:bg-rose-900/35'
      : 'border-red-200 bg-white text-red-700 hover:bg-red-50'
    return (
      <button
        type="button"
        className={`inline-flex min-w-28 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${disconnectClassName}`}
        onClick={onDisconnect}
        disabled={disabled}
      >
        {pendingKind === 'disconnect' ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <Unplug className="h-4 w-4" aria-hidden="true" />
        )}
        Disconnect
      </button>
    )
  }

  const connectClassName = surface === 'embedded'
    ? 'border border-sky-300/25 bg-sky-900/55 text-sky-50 hover:border-sky-200/40 hover:bg-sky-900/75'
    : 'bg-blue-600 text-white hover:bg-blue-700'
  return (
    <button
      type="button"
      className={`inline-flex min-w-28 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${connectClassName}`}
      onClick={onConnect}
      disabled={disabled}
    >
      {pendingKind === 'connect' ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <Plug className="h-4 w-4" aria-hidden="true" />
      )}
      Connect
    </button>
  )
}

export function PipedreamRemoveButton({
  isPending,
  disabled,
  title,
  onClick,
  surface = 'standalone',
}: {
  isPending: boolean
  disabled: boolean
  title: string
  onClick: () => void
  surface?: SettingsSurfaceVariant
}) {
  const className = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/20 text-slate-300 hover:border-slate-100/35 hover:bg-slate-900/40'
    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
  return (
    <button
      type="button"
      className={`inline-flex min-w-24 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-45 ${className}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {isPending ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <Trash2 className="h-4 w-4" aria-hidden="true" />
      )}
      Remove
    </button>
  )
}

export function AgentConnectionAvatar({
  agent,
  surface = 'standalone',
}: {
  agent: PipedreamAppAgentConnection
  surface?: SettingsSurfaceVariant
}) {
  if (agent.avatarUrl) {
    return (
      <img
        src={agent.avatarUrl}
        alt=""
        className="h-9 w-9 rounded-full border border-slate-200 bg-white object-cover"
        loading="lazy"
      />
    )
  }

  const className = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-900 text-slate-200'
    : 'border-slate-200 bg-white text-slate-700'
  return (
    <span className={`inline-flex h-9 w-9 items-center justify-center rounded-full border text-xs font-semibold uppercase ${className}`}>
      {agent.name.slice(0, 2)}
    </span>
  )
}

export function resolvePipedreamAppsErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof HttpError && typeof error.body === 'object' && error.body && 'error' in error.body) {
    const message = error.body.error
    if (typeof message === 'string' && message.trim()) {
      return message
    }
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return fallback
}
