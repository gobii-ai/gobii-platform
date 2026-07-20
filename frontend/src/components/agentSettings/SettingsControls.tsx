import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ElementType, HTMLAttributes, ReactNode } from 'react'

type SettingsSurface = 'standalone' | 'embedded'
type SettingsTone = 'primary' | 'neutral' | 'success' | 'warning' | 'danger'
type SettingsSize = 'sm' | 'md'

const embeddedToneClasses: Record<SettingsTone, string> = {
  primary: 'border-sky-300/25 bg-sky-900/55 text-sky-50 hover:border-sky-200/40 hover:bg-sky-900/75',
  neutral: 'border-slate-200/25 bg-slate-900/35 text-slate-100 hover:border-slate-100/35 hover:bg-slate-900/55 hover:text-white',
  success: 'border-emerald-300/25 bg-emerald-900/50 text-emerald-50 hover:border-emerald-200/40 hover:bg-emerald-900/70',
  warning: 'border-amber-300/25 bg-amber-950/30 text-amber-200 hover:border-amber-200/40 hover:bg-amber-900/45',
  danger: 'border-rose-300/25 bg-rose-950/35 text-rose-200 hover:border-rose-200/40 hover:bg-rose-900/50',
}

const standaloneToneClasses: Record<SettingsTone, string> = {
  primary: 'border-blue-600 bg-blue-600 text-white hover:bg-blue-700',
  neutral: 'border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:text-slate-900',
  success: 'border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-700',
  warning: 'border-amber-500 bg-amber-500 text-white hover:bg-amber-600',
  danger: 'border-rose-600 bg-rose-600 text-white hover:bg-rose-700',
}

const sizeClasses: Record<SettingsSize, string> = {
  sm: 'gap-1.5 rounded-md px-3 py-1.5 text-xs',
  md: 'gap-2 rounded-lg px-3 py-2 text-sm',
}

type SettingsControlStyle = {
  surface?: SettingsSurface
  tone?: SettingsTone
  size?: SettingsSize
  responsive?: boolean
  className?: string
}

export function getSettingsActionButtonClassName(options: SettingsControlStyle = {}) {
  const { surface = 'embedded', tone = 'neutral', size = 'md', responsive = false, className } = options
  const toneClasses = surface === 'embedded' ? embeddedToneClasses : standaloneToneClasses
  return [
    'inline-flex items-center justify-center border font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/50 disabled:cursor-not-allowed disabled:opacity-50',
    sizeClasses[size],
    toneClasses[tone],
    responsive ? 'w-full sm:w-auto' : '',
    className,
  ].filter(Boolean).join(' ')
}

type SettingsActionButtonProps = SettingsControlStyle & {
  as?: ElementType
  children: ReactNode
} & ButtonHTMLAttributes<HTMLButtonElement>
  & AnchorHTMLAttributes<HTMLAnchorElement>

export function SettingsActionButton({
  as: Component = 'button',
  surface = 'embedded',
  tone = 'neutral',
  size = 'md',
  responsive = false,
  className,
  children,
  type,
  ...rest
}: SettingsActionButtonProps) {
  return (
    <Component
      type={Component === 'button' ? (type ?? 'button') : type}
      className={getSettingsActionButtonClassName({ surface, tone, size, responsive, className })}
      {...rest}
    >
      {children}
    </Component>
  )
}

const badgeToneClasses: Record<SettingsTone, Record<SettingsSurface, string>> = {
  primary: { embedded: 'border-sky-300/20 bg-sky-950/35 text-sky-200', standalone: 'border-blue-200 bg-blue-50 text-blue-700' },
  neutral: { embedded: 'border-slate-300/20 bg-slate-900/35 text-slate-300', standalone: 'border-slate-200 bg-white text-slate-600' },
  success: { embedded: 'border-emerald-300/20 bg-emerald-950/35 text-emerald-200', standalone: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  warning: { embedded: 'border-amber-300/20 bg-amber-950/35 text-amber-200', standalone: 'border-amber-200 bg-amber-50 text-amber-800' },
  danger: { embedded: 'border-rose-300/20 bg-rose-950/35 text-rose-200', standalone: 'border-rose-200 bg-rose-50 text-rose-700' },
}

export function getSettingsStatusBadgeClassName(options: Pick<SettingsControlStyle, 'surface' | 'tone' | 'className'> = {}) {
  const { surface = 'embedded', tone = 'neutral', className } = options
  return [
    'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold',
    badgeToneClasses[tone][surface],
    className,
  ].filter(Boolean).join(' ')
}

export function SettingsStatusBadge({
  surface = 'embedded',
  tone = 'neutral',
  className,
  children,
  ...rest
}: {
  surface?: SettingsSurface
  tone?: SettingsTone
  className?: string
  children: ReactNode
} & HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={getSettingsStatusBadgeClassName({ surface, tone, className })}
      {...rest}
    >
      {children}
    </span>
  )
}
