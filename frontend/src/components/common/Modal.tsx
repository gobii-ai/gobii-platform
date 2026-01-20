import type { ReactNode } from 'react'
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { LucideIcon } from 'lucide-react'
import { Info, X } from 'lucide-react'

type ModalProps = {
  title: string
  subtitle?: string
  children: ReactNode
  footer?: ReactNode
  onClose: () => void
  widthClass?: string
  icon?: LucideIcon | null
  iconBgClass?: string
  iconColorClass?: string
  bodyClassName?: string
  containerClassName?: string
  panelClassName?: string
}

export function Modal({
  title,
  subtitle,
  children,
  footer,
  onClose,
  widthClass = 'sm:max-w-2xl',
  icon: Icon = Info,
  iconBgClass = 'bg-blue-100',
  iconColorClass = 'text-blue-600',
  bodyClassName = '',
  containerClassName = '',
  panelClassName = '',
}: ModalProps) {
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    document.addEventListener('keydown', handleKey)
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKey)
      document.body.style.overflow = originalOverflow
    }
  }, [onClose])

  if (typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div className="fixed inset-0 z-50 overflow-y-auto">
      <div
        className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm"
        onClick={onClose}
        role="presentation"
        aria-hidden="true"
      />
      <div className={`flex min-h-full items-start justify-center p-4 pb-20 text-center sm:items-center sm:p-6 sm:pb-6 sm:text-left ${containerClassName}`}>
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="modal-title"
          className={`relative z-50 w-full transform overflow-hidden rounded-2xl bg-white text-left shadow-2xl transition-all sm:my-8 ${widthClass} ${panelClassName}`}
        >
          <div className="px-6 py-5 sm:px-8">
            <div className="sm:flex sm:items-start sm:gap-4">
              {Icon && (
                <div className={`mx-auto flex h-12 w-12 items-center justify-center rounded-full ${iconBgClass} sm:mx-0 sm:h-10 sm:w-10`}>
                  <Icon aria-hidden="true" className={`h-5 w-5 ${iconColorClass}`} strokeWidth={2} />
                </div>
              )}
              <div className="mt-3 w-full text-center sm:mt-0 sm:text-left">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-900" id="modal-title">
                      {title}
                    </h2>
                    {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
                  </div>
                  <button
                    type="button"
                    className="text-slate-400 hover:text-slate-500"
                    onClick={onClose}
                    aria-label="Close dialog"
                  >
                    <X className="h-5 w-5" strokeWidth={2} />
                  </button>
                </div>
                <div className={`mt-4 max-h-[70vh] overflow-y-auto pr-1 text-left ${bodyClassName}`}>{children}</div>
              </div>
            </div>
          </div>
          {footer && (
            <div className="flex flex-col gap-3 border-t border-slate-100 bg-slate-50 px-6 py-4 sm:flex-row-reverse sm:items-center sm:px-8">
              {footer}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
