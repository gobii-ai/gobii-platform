import { AlertTriangle } from 'lucide-react'
import type { ReactNode } from 'react'

type ImmersivePageFrameLayout = 'main' | 'sidebar-shell'

type ImmersivePageFrameProps = {
  layout?: ImmersivePageFrameLayout
  children?: ReactNode
  loading?: boolean
  loadingLabel?: ReactNode
  error?: ReactNode | boolean
  errorFallback?: ReactNode
  maxWidthClass?: string
  className?: string
}

function getFrameClassName({
  layout,
  maxWidthClass,
  className,
}: {
  layout: ImmersivePageFrameLayout
  maxWidthClass: string
  className?: string
}) {
  return [
    layout === 'sidebar-shell' ? 'w-full px-1 pb-4' : `mx-auto w-full ${maxWidthClass} px-4 pb-6`,
    className,
  ].filter(Boolean).join(' ')
}

export function ImmersivePageFrame({
  layout = 'main',
  children,
  loading = false,
  loadingLabel = 'Loading…',
  error = null,
  errorFallback = 'Unable to load this page right now.',
  maxWidthClass = 'max-w-5xl',
  className,
}: ImmersivePageFrameProps) {
  const frameClassName = getFrameClassName({ layout, maxWidthClass, className })

  if (loading) {
    return (
      <div
        className={[
          frameClassName,
          'flex items-center justify-center',
          layout === 'sidebar-shell' ? 'min-h-[24rem]' : 'min-h-[40vh]',
        ].join(' ')}
      >
        <p className="text-sm font-medium text-slate-300">{loadingLabel}</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className={frameClassName}>
        <section
          className="flex items-start gap-3 rounded-3xl border border-rose-300/30 bg-rose-500/10 px-5 py-4 text-sm text-rose-100 backdrop-blur-xl"
          role="alert"
        >
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
          <div>{error === true ? errorFallback : error}</div>
        </section>
      </div>
    )
  }

  return <div className={frameClassName}>{children}</div>
}
