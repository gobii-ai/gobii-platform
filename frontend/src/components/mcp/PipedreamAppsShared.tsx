import { useEffect, useState, type ReactNode } from 'react'
import { Loader2, Plug, Search, Sparkles, Trash2, Unplug } from 'lucide-react'

import { HttpError } from '../../api/http'
import type { PipedreamAppAgentConnection, PipedreamAppSummary } from '../../api/mcp'
import { ImmersiveDialog } from '../common/ImmersiveDialog'
export { useIsMobile } from '../../hooks/useIsMobile'

export type PipedreamStatusMessage = {
  text: string
  tone?: 'error' | 'info'
} | null

type PipedreamAppIconProps = {
  app: PipedreamAppSummary
  size?: 'sm' | 'md'
}

export function PipedreamAppIcon({ app, size = 'md' }: PipedreamAppIconProps) {
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

  return (
    <span className={`inline-flex items-center justify-center border border-slate-200 bg-slate-50 font-semibold uppercase text-slate-700 ${sizeClass}`}>
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

export function PipedreamStatusBanner({ statusMessage }: { statusMessage: PipedreamStatusMessage }) {
  if (!statusMessage) {
    return null
  }
  const toneClass = statusMessage.tone === 'error'
    ? 'border-red-200 bg-red-50 text-red-700'
    : 'border-blue-200 bg-blue-50 text-blue-800'
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${toneClass}`}>
      {statusMessage.text}
    </div>
  )
}

export function PipedreamSearchInput({
  value,
  onChange,
  isFetching,
  disabled,
}: {
  value: string
  onChange: (value: string) => void
  isFetching: boolean
  disabled: boolean
}) {
  return (
    <label className="relative block text-sm text-slate-500">
      <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
        {isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" aria-hidden="true" />}
      </span>
      <input
        type="search"
        className="w-full rounded-lg border border-slate-300 bg-white py-3 pl-10 pr-3 text-sm text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-blue-500"
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
  children,
}: {
  isMobile: boolean
  children: ReactNode
}) {
  return (
    <div className={`overflow-hidden rounded-lg border border-slate-200 bg-white ${isMobile ? '' : 'max-h-[28rem] overflow-y-auto'}`}>
      <div className="divide-y divide-slate-200">
        {children}
      </div>
    </div>
  )
}

export function PipedreamLoadingState({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-5 text-sm text-slate-600">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </div>
  )
}

export function PipedreamEmptyState({ label }: { label: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-5 text-sm text-slate-600">
      {label}
    </div>
  )
}

export function PipedreamErrorState({
  error,
  fallback,
}: {
  error: unknown
  fallback: string
}) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {resolvePipedreamAppsErrorMessage(error, fallback)}
    </div>
  )
}

export function PipedreamAppSummaryCell({
  app,
}: {
  app: PipedreamAppSummary
}) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <PipedreamAppIcon app={app} />
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-slate-900">{app.name}</p>
        {app.description ? <p className="mt-1 line-clamp-2 text-sm text-slate-600">{app.description}</p> : null}
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
}: {
  connected: boolean
  pendingKind: 'connect' | 'disconnect' | null
  disabled: boolean
  onConnect: () => void
  onDisconnect: () => void
}) {
  if (connected) {
    return (
      <button
        type="button"
        className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-50 disabled:opacity-60"
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

  return (
    <button
      type="button"
      className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
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
}: {
  isPending: boolean
  disabled: boolean
  title: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className="inline-flex min-w-24 items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-45"
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

export function AgentConnectionAvatar({ agent }: { agent: PipedreamAppAgentConnection }) {
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

  return (
    <span className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-xs font-semibold uppercase text-slate-700">
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
