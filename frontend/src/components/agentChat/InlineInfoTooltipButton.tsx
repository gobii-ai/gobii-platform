import { useId, useLayoutEffect, useRef, useState } from 'react'

import { Info } from 'lucide-react'
import { Button } from 'react-aria-components'
import { createPortal } from 'react-dom'

type InlineInfoTooltipButtonProps = {
  label: string
  description: string
  disabled?: boolean
}

export function InlineInfoTooltipButton({
  label,
  description,
  disabled = false,
}: InlineInfoTooltipButtonProps) {
  const [isPinnedOpen, setIsPinnedOpen] = useState(false)
  const [isHovered, setIsHovered] = useState(false)
  const [isFocused, setIsFocused] = useState(false)
  const [tooltipPosition, setTooltipPosition] = useState<{ top: number; left: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const tooltipId = useId()
  const isOpen = !disabled && (isPinnedOpen || isHovered || isFocused)

  useLayoutEffect(() => {
    if (!isOpen || typeof window === 'undefined') {
      return
    }

    const updatePosition = () => {
      const trigger = triggerRef.current
      if (!trigger) {
        return
      }
      const rect = trigger.getBoundingClientRect()
      const tooltipWidth = Math.min(288, window.innerWidth - 32)
      const left = Math.min(
        window.innerWidth - tooltipWidth - 16,
        Math.max(16, rect.right - tooltipWidth),
      )
      setTooltipPosition({
        top: rect.bottom + 6,
        left,
      })
    }

    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [isOpen])

  return (
    <div
      className="relative shrink-0"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => {
        setIsHovered(false)
        setIsPinnedOpen(false)
      }}
    >
      <Button
        ref={triggerRef}
        aria-label={label}
        aria-describedby={isOpen ? tooltipId : undefined}
        aria-expanded={isPinnedOpen}
        className="inline-flex h-6 w-6 items-center justify-center rounded-full text-slate-400 transition hover:bg-white hover:text-slate-600 focus:bg-white focus:text-slate-600"
        isDisabled={disabled}
        onPress={() => setIsPinnedOpen((isOpen) => !isOpen)}
        onFocus={() => setIsFocused(true)}
        onBlur={() => {
          setIsFocused(false)
          setIsPinnedOpen(false)
        }}
      >
        <Info className="h-3.5 w-3.5" aria-hidden="true" />
      </Button>
      {isOpen && tooltipPosition && typeof document !== 'undefined'
        ? createPortal(
            <div
              id={tooltipId}
              role="tooltip"
              className="pointer-events-none fixed z-[80] w-72 max-w-[min(22rem,calc(100vw-2rem))] rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs leading-5 text-slate-700 shadow-xl"
              style={{
                top: `${tooltipPosition.top}px`,
                left: `${tooltipPosition.left}px`,
              }}
            >
              {description}
            </div>,
            document.body,
          )
        : null}
    </div>
  )
}
