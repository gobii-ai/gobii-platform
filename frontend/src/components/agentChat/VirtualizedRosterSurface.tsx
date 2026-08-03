import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useRef, type ReactNode } from 'react'

export type VirtualRosterRow = {
  key: string
  agentId?: string
  agentIds?: string[]
  content: ReactNode
}

type VirtualizedRosterSurfaceProps = {
  rows: VirtualRosterRow[]
  className: string
  estimateSize: number
  gap: number
  overscan: number
  role?: string
  variant?: 'sidebar' | 'drawer'
  enabled?: boolean
  scrollToAgentId?: string | null
  onScrolledToAgent?: (agentId: string) => void
  onViewportWidthChange?: (width: number) => void
}

export function VirtualizedRosterSurface({
  rows,
  className,
  estimateSize,
  gap,
  overscan,
  role,
  variant,
  enabled = true,
  scrollToAgentId,
  onScrolledToAgent,
  onViewportWidthChange,
}: VirtualizedRosterSurfaceProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateSize,
    getItemKey: (index) => rows[index]?.key ?? index,
    measureElement: (element) => element.getBoundingClientRect().height,
    overscan,
    gap,
    enabled,
  })

  useEffect(() => {
    const element = scrollRef.current
    if (!element || !onViewportWidthChange || typeof ResizeObserver === 'undefined') {
      return
    }
    const update = () => onViewportWidthChange(element.clientWidth)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [onViewportWidthChange])

  useEffect(() => {
    if (!enabled || !scrollToAgentId || typeof window === 'undefined') {
      return
    }
    const index = rows.findIndex((row) => (
      row.agentId === scrollToAgentId || row.agentIds?.includes(scrollToAgentId)
    ))
    if (index < 0) {
      return
    }
    virtualizer.scrollToIndex(index, { align: 'center' })
    let nextFrame: number | null = null
    const frame = window.requestAnimationFrame(() => {
      nextFrame = window.requestAnimationFrame(() => {
        const selectorId = typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
          ? CSS.escape(scrollToAgentId)
          : scrollToAgentId.replace(/["\\]/g, '\\$&')
        const rosterItem = scrollRef.current?.querySelector<HTMLElement>(
          `[data-agent-roster-item-id="${selectorId}"]`,
        )
        if (rosterItem) {
          const prefersReducedMotion = typeof window.matchMedia === 'function'
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches
          rosterItem.scrollIntoView({
            block: 'center',
            inline: 'nearest',
            behavior: prefersReducedMotion ? 'auto' : 'smooth',
          })
          onScrolledToAgent?.(scrollToAgentId)
        }
      })
    })
    return () => {
      window.cancelAnimationFrame(frame)
      if (nextFrame !== null) window.cancelAnimationFrame(nextFrame)
    }
  }, [enabled, onScrolledToAgent, rows, scrollToAgentId, virtualizer])

  return (
    <div
      ref={scrollRef}
      className={className}
      role={role}
      data-variant={variant}
      data-virtualized="true"
    >
      <div
        className="agent-roster-virtual-canvas"
        style={{ height: virtualizer.getTotalSize() }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const row = rows[virtualRow.index]
          if (!row) return null
          return (
            <div
              key={row.key}
              ref={virtualizer.measureElement}
              data-index={virtualRow.index}
              className="agent-roster-virtual-row"
              style={{ transform: `translateY(${virtualRow.start}px)` }}
            >
              {row.content}
            </div>
          )
        })}
      </div>
    </div>
  )
}
