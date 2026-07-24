import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { LucideIcon } from 'lucide-react'

export type FixedContextMenuPosition = { x: number; y: number }

export type FixedContextMenuItem = {
  label: string
  icon: LucideIcon
  onSelect: () => void
  disabled?: boolean
}

type FixedContextMenuProps = {
  position: FixedContextMenuPosition
  ariaLabel: string
  items: FixedContextMenuItem[]
  onClose: () => void
}

function clampPosition(position: FixedContextMenuPosition, itemCount: number): FixedContextMenuPosition {
  return {
    x: Math.min(Math.max(8, position.x), Math.max(8, window.innerWidth - 216)),
    y: Math.min(Math.max(8, position.y), Math.max(8, window.innerHeight - 40 - itemCount * 36)),
  }
}

export function FixedContextMenu({ position, ariaLabel, items, onClose }: FixedContextMenuProps) {
  const menuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) onClose()
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', handlePointerDown, true)
    document.addEventListener('keydown', handleKeyDown, true)
    window.addEventListener('resize', onClose)
    window.addEventListener('scroll', onClose, true)
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true)
      document.removeEventListener('keydown', handleKeyDown, true)
      window.removeEventListener('resize', onClose)
      window.removeEventListener('scroll', onClose, true)
    }
  }, [onClose])

  if (typeof document === 'undefined') return null
  const clampedPosition = clampPosition(position, items.length)

  return createPortal(
    <div
      ref={menuRef}
      className="fixed-context-menu sidebar-settings__menu"
      role="menu"
      aria-label={ariaLabel}
      style={{ left: clampedPosition.x, top: clampedPosition.y }}
    >
      {items.map(({ label, icon: Icon, onSelect, disabled }) => (
        <button
          key={label}
          type="button"
          role="menuitem"
          className="fixed-context-menu__item sidebar-settings__link"
          onClick={() => {
            onClose()
            onSelect()
          }}
          disabled={disabled}
        >
          <Icon className="sidebar-settings__link-icon" aria-hidden="true" />
          <span>{label}</span>
        </button>
      ))}
    </div>,
    document.body,
  )
}
