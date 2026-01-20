import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { X } from 'lucide-react'

type AgentChatMobileSheetProps = {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  icon?: LucideIcon | null
  headerAccessory?: ReactNode
  children: ReactNode
  ariaLabel?: string
  keepMounted?: boolean
  bodyPadding?: boolean
}

export function AgentChatMobileSheet({
  open,
  onClose,
  title,
  subtitle,
  icon: Icon,
  headerAccessory,
  children,
  ariaLabel,
  keepMounted = false,
  bodyPadding = true,
}: AgentChatMobileSheetProps) {
  useEffect(() => {
    if (!open) {
      return
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = originalOverflow
    }
  }, [onClose, open])

  if (typeof document === 'undefined') {
    return null
  }

  if (!open && !keepMounted) {
    return null
  }

  return createPortal(
    <div className={`agent-mobile-sheet ${open ? 'agent-mobile-sheet--open' : ''}`}>
      <div
        className={`agent-mobile-sheet-backdrop ${open ? 'agent-mobile-sheet-backdrop--open' : ''}`}
        role="presentation"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        className={`agent-mobile-sheet-panel ${open ? 'agent-mobile-sheet-panel--open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel || title}
        aria-hidden={!open}
      >
        <div className="agent-mobile-sheet-header">
          <div className="agent-mobile-sheet-heading">
            {Icon ? (
              <div className="agent-mobile-sheet-icon" aria-hidden="true">
                <Icon size={18} />
              </div>
            ) : null}
            <div className="agent-mobile-sheet-titles">
              <h2 className="agent-mobile-sheet-title">{title}</h2>
              {subtitle ? <p className="agent-mobile-sheet-subtitle">{subtitle}</p> : null}
            </div>
            {headerAccessory ? <div className="agent-mobile-sheet-accessory">{headerAccessory}</div> : null}
          </div>
          <button type="button" className="agent-mobile-sheet-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className={`agent-mobile-sheet-body${bodyPadding ? ' agent-mobile-sheet-body--padded' : ''}`}>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  )
}
