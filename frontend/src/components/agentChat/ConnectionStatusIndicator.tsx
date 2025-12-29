export type ConnectionStatusTone = 'connected' | 'connecting' | 'reconnecting' | 'offline' | 'error'

const STATUS_STYLES: Record<ConnectionStatusTone, { dot: string; text: string; pulse: boolean }> = {
  connected: { dot: 'bg-emerald-500', text: 'text-emerald-700', pulse: false },
  connecting: { dot: 'bg-sky-500', text: 'text-sky-700', pulse: true },
  reconnecting: { dot: 'bg-amber-500', text: 'text-amber-700', pulse: true },
  offline: { dot: 'bg-slate-600', text: 'text-slate-600', pulse: false },
  error: { dot: 'bg-rose-500', text: 'text-rose-700', pulse: false },
}

type ConnectionStatusIndicatorProps = {
  status: ConnectionStatusTone
  label: string
  detail?: string | null
  className?: string
}

export function ConnectionStatusIndicator({
  status,
  label,
  detail,
  className = '',
}: ConnectionStatusIndicatorProps) {
  const { dot, text, pulse } = STATUS_STYLES[status]
  const title = detail ? `${label} - ${detail}` : label

  return (
    <span
      className={`inline-flex items-center gap-2 text-[0.62rem] font-semibold uppercase tracking-[0.24em] ${text} ${className}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      title={title}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${dot} ${pulse ? 'animate-pulse' : ''}`}
        aria-hidden="true"
      />
      <span>{label}</span>
    </span>
  )
}
