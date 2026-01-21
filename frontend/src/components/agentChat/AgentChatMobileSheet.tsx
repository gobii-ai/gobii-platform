import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { X } from 'lucide-react'

const DRAG_CLOSE_THRESHOLD = 120

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
  const [dragOffset, setDragOffset] = useState(0)
  const [isDragging, setIsDragging] = useState(false)
  const dragStartYRef = useRef(0)
  const dragOffsetRef = useRef(0)
  const draggingRef = useRef(false)

  const setDragOffsetValue = useCallback((value: number) => {
    dragOffsetRef.current = value
    setDragOffset(value)
  }, [])

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

  useEffect(() => {
    if (open) {
      return
    }
    setIsDragging(false)
    draggingRef.current = false
    setDragOffsetValue(0)
  }, [open, setDragOffsetValue])

  const handleGrabberPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!open) {
        return
      }
      if (event.pointerType === 'mouse' && event.button !== 0) {
        return
      }
      dragStartYRef.current = event.clientY
      draggingRef.current = true
      setIsDragging(true)
      setDragOffsetValue(0)
      event.currentTarget.setPointerCapture(event.pointerId)
    },
    [open, setDragOffsetValue],
  )

  const handleGrabberPointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingRef.current) {
        return
      }
      const delta = event.clientY - dragStartYRef.current
      const nextOffset = Math.max(0, delta)
      setDragOffsetValue(nextOffset)
    },
    [setDragOffsetValue],
  )

  const handleGrabberPointerUp = useCallback(() => {
    if (!draggingRef.current) {
      return
    }
    draggingRef.current = false
    setIsDragging(false)
    const offset = dragOffsetRef.current
    setDragOffsetValue(0)
    if (offset > DRAG_CLOSE_THRESHOLD) {
      onClose()
      return
    }
  }, [isDragging, onClose, setDragOffsetValue])

  if (typeof document === 'undefined') {
    return null
  }

  if (!open && !keepMounted) {
    return null
  }

  const panelStyle = isDragging ? { transform: `translateY(${dragOffset}px)` } : undefined

  return createPortal(
    <div className={`agent-mobile-sheet ${open ? 'agent-mobile-sheet--open' : ''}`}>
      <div
        className={`agent-mobile-sheet-backdrop ${open ? 'agent-mobile-sheet-backdrop--open' : ''}`}
        role="presentation"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        className={[
          'agent-mobile-sheet-panel',
          open ? 'agent-mobile-sheet-panel--open' : '',
          isDragging ? 'agent-mobile-sheet-panel--dragging' : '',
        ].join(' ')}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel || title}
        aria-hidden={!open}
        style={panelStyle}
      >
        <div
          className="agent-mobile-sheet-grabber"
          role="presentation"
          aria-hidden="true"
          onPointerDown={handleGrabberPointerDown}
          onPointerMove={handleGrabberPointerMove}
          onPointerUp={handleGrabberPointerUp}
          onPointerCancel={handleGrabberPointerUp}
        >
          <div className="agent-mobile-sheet-grabber-bar" />
        </div>
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
