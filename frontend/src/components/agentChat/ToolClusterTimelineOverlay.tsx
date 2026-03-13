import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

import { slugify } from '../../util/slugify'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import { ActivityEntryList } from './ActivityEntryList'
import type { ToolEntryDisplay } from './tooling/types'

function isMobileViewport() {
  if (typeof window === 'undefined') {
    return false
  }

  return window.innerWidth < 768
}

type ToolClusterTimelineOverlayProps = {
  open: boolean
  overlayId: string
  title: string
  subtitle?: string
  entries: ToolEntryDisplay[]
  initialOpenEntryId?: string | null
  onClose: () => void
}

export function ToolClusterTimelineOverlay({
  open,
  overlayId,
  title,
  subtitle = 'Action timeline',
  entries,
  initialOpenEntryId = null,
  onClose,
}: ToolClusterTimelineOverlayProps) {
  const [, setIsMobile] = useState(isMobileViewport)
  const titleId = useMemo(() => `tool-cluster-timeline-title-${slugify(overlayId)}`, [overlayId])
  const dialogId = useMemo(() => `tool-cluster-timeline-dialog-${slugify(overlayId)}`, [overlayId])

  useEffect(() => {
    if (!open || typeof window === 'undefined') {
      return undefined
    }

    const handleResize = () => {
      setIsMobile(isMobileViewport())
    }

    handleResize()
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [open])

  useEffect(() => {
    if (!open) {
      return undefined
    }

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
  }, [onClose, open])

  if (!open) {
    return null
  }

  const shouldUseMobileSheet = isMobileViewport()
  const overlayBody = (
    <div className="tool-cluster-timeline-body">
      <ActivityEntryList entries={entries} initialOpenEntryId={initialOpenEntryId} />
    </div>
  )

  if (shouldUseMobileSheet) {
    return (
      <AgentChatMobileSheet
        open={open}
        onClose={onClose}
        title={title}
        subtitle={subtitle}
        ariaLabel={title}
        bodyPadding={false}
      >
        {overlayBody}
      </AgentChatMobileSheet>
    )
  }

  if (typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div className="tool-cluster-timeline-overlay">
      <div className="tool-cluster-timeline-backdrop" role="presentation" onClick={onClose} />
      <div className="tool-cluster-timeline-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} id={dialogId}>
        <div className="tool-cluster-timeline-header">
          <div className="tool-cluster-timeline-title">
            <span className="tool-cluster-timeline-count" id={titleId}>
              {title}
            </span>
            <span className="tool-cluster-timeline-subtitle">{subtitle}</span>
          </div>
          <button type="button" className="tool-cluster-timeline-close" onClick={onClose} aria-label="Close timeline">
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
        {overlayBody}
      </div>
    </div>,
    document.body,
  )
}
