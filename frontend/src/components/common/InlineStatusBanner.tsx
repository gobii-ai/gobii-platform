import type { ElementType, ReactNode } from 'react'

import type { SettingsSurfaceVariant } from './SettingsSurface'

type InlineStatusBannerVariant = 'success' | 'error' | 'warning' | 'info'

type InlineStatusBannerProps = {
  variant: InlineStatusBannerVariant
  surface?: SettingsSurfaceVariant
  icon?: ElementType
  children: ReactNode
  role?: 'alert' | 'status' | 'note'
  density?: 'default' | 'compact'
  className?: string
}

const embeddedClassNames: Record<InlineStatusBannerVariant, string> = {
  success: 'border-emerald-300/25 bg-emerald-950/30 text-emerald-100',
  error: 'border-rose-300/25 bg-rose-950/30 text-rose-100',
  warning: 'border-amber-300/30 bg-amber-950/20 text-amber-100',
  info: 'border-sky-300/25 bg-sky-950/30 text-sky-100',
}

const standaloneClassNames: Record<InlineStatusBannerVariant, string> = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  error: 'border-red-200 bg-red-50 text-red-700',
  warning: 'border-amber-200 bg-amber-50 text-amber-900',
  info: 'border-blue-200 bg-blue-50 text-blue-700',
}

export function InlineStatusBanner({
  variant,
  surface = 'standalone',
  icon: Icon,
  children,
  role,
  density = 'default',
  className,
}: InlineStatusBannerProps) {
  const toneClassName = surface === 'embedded' ? embeddedClassNames[variant] : standaloneClassNames[variant]

  return (
    <div
      className={[
        density === 'compact' ? 'rounded-xl border px-4 py-2 text-sm' : 'rounded-xl border px-4 py-3 text-sm',
        Icon ? 'flex items-start gap-2' : '',
        toneClassName,
        className,
      ].filter(Boolean).join(' ')}
      role={role}
    >
      {Icon ? <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> : null}
      <div>{children}</div>
    </div>
  )
}
