import { HttpError } from '../../api/http'
import type { PipedreamAppSummary } from '../../api/mcp'

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
