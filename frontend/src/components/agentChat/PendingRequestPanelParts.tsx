import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Inbox, Loader2, ShieldCheck } from 'lucide-react'

import { InlineStatusBanner } from '../common/InlineStatusBanner'
import { getSettingsSurfaceClassName } from '../common/SettingsSurface'

export function formatPendingRequestDate(value?: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

export function usePendingRequestSelection<T extends { id: string }>(items: T[]) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  useEffect(() => {
    const itemIds = new Set(items.map((item) => item.id))
    setSelectedIds((current) => {
      const next = new Set([...current].filter((id) => itemIds.has(id)))
      return next.size === current.size ? current : next
    })
  }, [items])

  const selectedItems = useMemo(
    () => items.filter((item) => selectedIds.has(item.id)),
    [items, selectedIds],
  )
  const toggleSelected = useCallback((id: string, selected: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (selected) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])
  const selectAll = useCallback(() => setSelectedIds(new Set(items.map((item) => item.id))), [items])
  const clearSelected = useCallback(() => setSelectedIds(new Set()), [])
  const removeSelected = useCallback((ids: string[]) => {
    const removed = new Set(ids)
    setSelectedIds((current) => new Set([...current].filter((id) => !removed.has(id))))
  }, [])

  return {
    selectedIds,
    selectedItems,
    allSelected: items.length > 0 && selectedIds.size === items.length,
    toggleSelected,
    selectAll,
    clearSelected,
    removeSelected,
  }
}

type ReviewFooterProps = {
  description: string
  showSummary?: boolean
  disabled: boolean
  busy: boolean
  secondaryLabel: string
  secondaryBusyLabel: string
  primaryLabel: string
  primaryBusyLabel: string
  primaryDisabled?: boolean
  theme: 'contact' | 'secret'
  error?: string | null
  notice?: string | null
  onSecondary: () => void
  onPrimary: () => void
}

export function PendingRequestReviewFooter({
  description,
  showSummary = true,
  disabled,
  busy,
  secondaryLabel,
  secondaryBusyLabel,
  primaryLabel,
  primaryBusyLabel,
  primaryDisabled = false,
  theme,
  error,
  notice,
  onSecondary,
  onPrimary,
}: ReviewFooterProps) {
  const primaryColor = theme === 'contact'
    ? 'bg-amber-600 hover:bg-amber-700'
    : 'bg-sky-600 hover:bg-sky-700'
  return (
    <div className="space-y-2">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        {showSummary ? (
          <div className="hidden min-w-0 items-center gap-3 sm:flex">
            <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-violet-100 text-violet-700">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-900">Reviewing this request</p>
              <p className="text-sm text-slate-600">{description}</p>
            </div>
          </div>
        ) : null}
        <div className="flex flex-col-reverse gap-2 sm:ml-auto sm:flex-row sm:justify-end">
          <button
            type="button"
            disabled={disabled || busy}
            className="inline-flex w-full items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60 sm:w-32"
            onClick={onSecondary}
          >
            {busy ? secondaryBusyLabel : secondaryLabel}
          </button>
          <button
            type="button"
            disabled={disabled || busy || primaryDisabled}
            className={`inline-flex w-full items-center justify-center rounded-lg px-4 py-2 text-sm font-semibold text-white transition disabled:cursor-not-allowed disabled:opacity-60 sm:w-32 ${primaryColor}`}
            onClick={onPrimary}
          >
            {busy ? primaryBusyLabel : primaryLabel}
          </button>
        </div>
      </div>
      {error ? <p className="text-sm text-rose-600 sm:text-right">{error}</p> : null}
      {notice && !error ? <p className="text-sm text-amber-700 sm:text-right">{notice}</p> : null}
    </div>
  )
}

type EmbeddedRequestStateProps = {
  isLoading: boolean
  error: unknown
  isEmpty: boolean
  loadingLabel: string
  errorTitle: string
  emptyTitle: string
  emptyDescription: string
  emptyAction?: ReactNode
  children: ReactNode
}

export function EmbeddedPendingRequestState({
  isLoading,
  error,
  isEmpty,
  loadingLabel,
  errorTitle,
  emptyTitle,
  emptyDescription,
  emptyAction,
  children,
}: EmbeddedRequestStateProps) {
  if (isLoading) {
    return (
      <div className="flex min-h-[18rem] items-center justify-center text-sm text-slate-200/80">
        <div className="flex flex-col items-center gap-3 text-center">
          <Loader2 className="h-6 w-6 animate-spin text-slate-300/70" aria-hidden="true" />
          <p>{loadingLabel}</p>
        </div>
      </div>
    )
  }
  if (error) {
    return (
      <InlineStatusBanner variant="error" surface="embedded">
        <p className="font-medium">{errorTitle}</p>
        <p className="mt-1 text-rose-100/75">Try opening this agent again.</p>
      </InlineStatusBanner>
    )
  }
  if (isEmpty) {
    return (
      <div className={getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none', className: 'flex min-h-[18rem] items-center justify-center px-6 py-10 text-center' })}>
        <div className="max-w-sm space-y-4">
          <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200/20 bg-slate-900/45 text-slate-200">
            <Inbox className="h-5 w-5" aria-hidden="true" />
          </span>
          <div>
            <p className="text-sm font-semibold text-slate-100">{emptyTitle}</p>
            <p className="mt-1 text-sm text-slate-300">{emptyDescription}</p>
          </div>
          {emptyAction}
        </div>
      </div>
    )
  }
  return children
}

type EmbeddedSummaryProps = {
  count: number
  noun: string
  description: string
  actions: ReactNode
  footer?: ReactNode
  compact?: boolean
}

export function EmbeddedPendingRequestSummary({ count, noun, description, actions, footer, compact = false }: EmbeddedSummaryProps) {
  if (compact) {
    return (
      <div className={getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none', className: 'flex flex-col gap-3 px-4 py-4 text-slate-100 sm:flex-row sm:items-center sm:justify-between' })}>
        <div>
          <p className="text-sm font-semibold text-slate-100">
            {count} pending {noun} request{count === 1 ? '' : 's'}
          </p>
          <p className="mt-1 text-xs text-slate-400">{description}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      </div>
    )
  }
  return (
    <div className={getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none', className: 'px-4 py-4 text-slate-100' })}>
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-slate-200/20 bg-slate-900/45 text-slate-200">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-100">
              {count} pending {noun} request{count === 1 ? '' : 's'}
            </p>
            <p className="mt-1 text-sm text-slate-400">{description}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      </div>
      {footer}
    </div>
  )
}
