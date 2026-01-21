import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { CSSProperties, PointerEvent, ReactNode } from 'react'
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
  const [isExpanded, setIsExpanded] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [dragOffset, setDragOffset] = useState(0)
  const dragOffsetRef = useRef(0)
  const dragStartYRef = useRef<number | null>(null)
  const pointerIdRef = useRef<number | null>(null)

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
    setIsExpanded(false)
    setIsDragging(false)
    setDragOffset(0)
    dragOffsetRef.current = 0
    dragStartYRef.current = null
    pointerIdRef.current = null
  }, [open])

  if (typeof document === 'undefined') {
    return null
  }

  if (!open && !keepMounted) {
    return null
  }

  const panelClassName = [
    'agent-mobile-sheet-panel',
    open ? 'agent-mobile-sheet-panel--open' : '',
    isExpanded ? 'agent-mobile-sheet-panel--expanded' : '',
    isDragging ? 'agent-mobile-sheet-panel--dragging' : '',
  ]
    .filter(Boolean)
    .join(' ')

  const panelStyle: CSSProperties | undefined = isDragging
    ? ({ '--agent-mobile-sheet-translate': `${dragOffset}px` } as CSSProperties)
    : undefined

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!open) {
      return
    }
    pointerIdRef.current = event.pointerId
    dragStartYRef.current = event.clientY
    dragOffsetRef.current = 0
    setDragOffset(0)
    setIsDragging(true)
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!isDragging || pointerIdRef.current !== event.pointerId) {
      return
    }
    if (dragStartYRef.current === null) {
      return
    }
    const delta = event.clientY - dragStartYRef.current
    let nextOffset = delta
    if (nextOffset < 0) {
      nextOffset = isExpanded ? Math.min(nextOffset, 0) : Math.max(nextOffset, -120)
    } else {
      nextOffset = Math.min(nextOffset, 240)
    }
    dragOffsetRef.current = nextOffset
    setDragOffset(nextOffset)
  }

  const finishDrag = () => {
    const offset = dragOffsetRef.current
    const shouldClose = offset > 120
    const shouldExpand = !isExpanded && offset < -60
    const shouldCollapse = isExpanded && offset > 80
    if (shouldClose) {
      onClose()
    } else if (shouldExpand) {
      setIsExpanded(true)
    } else if (shouldCollapse) {
      setIsExpanded(false)
    }
    setIsDragging(false)
    setDragOffset(0)
    dragOffsetRef.current = 0
    dragStartYRef.current = null
    pointerIdRef.current = null
  }

  const handlePointerUp = (event: PointerEvent<HTMLDivElement>) => {
    if (!isDragging || pointerIdRef.current !== event.pointerId) {
      return
    }
    finishDrag()
  }

  const handlePointerCancel = (event: PointerEvent<HTMLDivElement>) => {
    if (!isDragging || pointerIdRef.current !== event.pointerId) {
      return
    }
    finishDrag()
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
        className={panelClassName}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel || title}
        aria-hidden={!open}
        style={panelStyle}
      >
        <div
          className="agent-mobile-sheet-grabber"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerCancel}
          aria-hidden="true"
        >
          <span className="agent-mobile-sheet-grabber-bar" />
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
