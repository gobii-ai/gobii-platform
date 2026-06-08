import type { ComponentPropsWithoutRef, ElementType, ReactNode } from 'react'

export type SettingsSurfaceVariant = 'embedded' | 'standalone'
export type SettingsSurfacePadding = 'none' | 'sm' | 'md' | 'lg'
export type SettingsSurfaceOverflow = 'visible' | 'hidden'

export const sharedSettingsGlassFrameClassName = 'settings-card-surface overflow-hidden rounded-2xl border'
export const embeddedSettingsSurfaceClassName = 'settings-card-surface--embedded border-slate-200/20 text-slate-100'
export const standaloneSettingsSurfaceClassName = 'settings-card-surface--standalone border-gray-200/70 text-gray-900'

const variantClassNames: Record<SettingsSurfaceVariant, string> = {
  embedded: embeddedSettingsSurfaceClassName,
  standalone: standaloneSettingsSurfaceClassName,
}

const paddingClassNames: Record<SettingsSurfacePadding, string> = {
  none: '',
  sm: 'p-4',
  md: 'px-5 py-4',
  lg: 'px-6 py-5',
}

const overflowClassNames: Record<SettingsSurfaceOverflow, string> = {
  visible: '',
  hidden: 'overflow-hidden',
}

export function getSettingsSurfaceClassName({
  variant = 'standalone',
  className,
  padding = 'none',
  overflow = 'hidden',
  roundedClassName = 'rounded-2xl',
  borderClassName,
  shadowClassName,
}: {
  variant?: SettingsSurfaceVariant
  className?: string
  padding?: SettingsSurfacePadding
  overflow?: SettingsSurfaceOverflow
  roundedClassName?: string
  borderClassName?: string
  shadowClassName?: string
} = {}) {
  const resolvedBorderClassName = borderClassName ?? (variant === 'embedded' ? 'border-slate-200/20' : 'border-gray-200/70')

  return [
    'settings-card-surface',
    variantClassNames[variant],
    overflowClassNames[overflow],
    roundedClassName,
    'border',
    resolvedBorderClassName,
    shadowClassName,
    paddingClassNames[padding],
    className,
  ].filter(Boolean).join(' ')
}

type SettingsSurfaceProps = {
  variant?: SettingsSurfaceVariant
  as?: ElementType
  className?: string
  padding?: SettingsSurfacePadding
  overflow?: SettingsSurfaceOverflow
  roundedClassName?: string
  borderClassName?: string
  shadowClassName?: string
  children: ReactNode
} & Omit<ComponentPropsWithoutRef<'div'>, 'className' | 'children'>

export function SettingsSurface({
  variant = 'standalone',
  as: Component = 'div',
  className,
  padding = 'none',
  overflow = 'hidden',
  roundedClassName,
  borderClassName,
  shadowClassName,
  children,
  ...rest
}: SettingsSurfaceProps) {
  return (
    <Component className={getSettingsSurfaceClassName({
      variant,
      className,
      padding,
      overflow,
      roundedClassName,
      borderClassName,
      shadowClassName,
    })}
    {...rest}
    >
      {children}
    </Component>
  )
}

type SurfaceHeaderProps = {
  variant?: SettingsSurfaceVariant
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  headingLevel?: 1 | 2 | 3 | 4
  className?: string
  titleClassName?: string
  subtitleClassName?: string
}

export function SurfaceHeader({
  variant = 'standalone',
  title,
  subtitle,
  actions,
  headingLevel = 2,
  className,
  titleClassName,
  subtitleClassName,
}: SurfaceHeaderProps) {
  const Heading = `h${headingLevel}` as ElementType
  const resolvedTitleClassName = titleClassName ?? (
    variant === 'embedded' ? 'text-lg font-semibold text-slate-50' : 'text-lg font-semibold text-slate-900'
  )
  const resolvedSubtitleClassName = subtitleClassName ?? (
    variant === 'embedded' ? 'text-sm text-slate-400' : 'text-sm text-slate-600'
  )

  return (
    <header className={[
      'flex flex-col gap-4 px-6 py-4 sm:flex-row sm:items-center sm:justify-between',
      variant === 'standalone' ? 'border-b border-gray-200/70' : '',
      className,
    ].filter(Boolean).join(' ')}
    >
      <div className="flex flex-col gap-1">
        <Heading className={resolvedTitleClassName}>{title}</Heading>
        {subtitle ? <p className={resolvedSubtitleClassName}>{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  )
}
