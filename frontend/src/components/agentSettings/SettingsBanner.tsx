import type { ReactNode } from 'react'

type SettingsBannerVariant = 'standalone' | 'embedded'

type SettingsBannerProps = {
  variant?: SettingsBannerVariant
  leading?: ReactNode
  eyebrow?: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  supportingContent?: ReactNode
  actions?: ReactNode
  headingId?: string
}

export function SettingsBanner({
  variant = 'standalone',
  leading = null,
  eyebrow,
  title,
  subtitle,
  supportingContent,
  actions,
  headingId,
}: SettingsBannerProps) {
  const isEmbedded = variant === 'embedded'
  const surfaceClassName = isEmbedded
    ? 'border-slate-200/70 bg-slate-950/60 text-slate-100'
    : 'border-gray-200/70 bg-white/78 text-gray-900'
  const eyebrowClassName = isEmbedded
    ? 'text-xs font-semibold uppercase tracking-[0.22em] text-slate-400'
    : 'text-xs font-semibold uppercase tracking-[0.18em] text-gray-500'
  const titleClassName = isEmbedded
    ? 'text-xl font-semibold text-slate-100'
    : 'text-2xl font-semibold text-gray-800'
  const subtitleClassName = isEmbedded
    ? 'mt-1 text-sm text-slate-300'
    : 'mt-1 text-sm text-gray-500'

  return (
    <header className="sticky top-0 z-20 py-1">
      <div className={`overflow-hidden rounded-2xl border backdrop-blur-xl ${surfaceClassName}`}>
        <div className="px-6 py-4">
          <div className={`flex items-start gap-3 ${leading ? '' : 'justify-between'}`}>
            {leading ? <div className="shrink-0">{leading}</div> : null}
            <div className="flex min-w-0 flex-1 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                {eyebrow ? <p className={eyebrowClassName}>{eyebrow}</p> : null}
                <h1 className={titleClassName} id={headingId}>
                  {title}
                </h1>
                {subtitle ? <p className={subtitleClassName}>{subtitle}</p> : null}
                {supportingContent ? <div className="mt-3">{supportingContent}</div> : null}
              </div>
              {actions ? (
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                  {actions}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </header>
  )
}
