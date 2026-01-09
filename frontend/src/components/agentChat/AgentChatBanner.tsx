import { useEffect, useRef, useState } from 'react'
import { Check, X } from 'lucide-react'

import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { normalizeHexColor } from '../../util/color'
import type { KanbanBoardSnapshot } from '../../types/agentChat'

export type ConnectionStatusTone = 'connected' | 'connecting' | 'reconnecting' | 'offline' | 'error'

type AgentChatBannerProps = {
  agentName: string
  agentAvatarUrl?: string | null
  agentColorHex?: string | null
  connectionStatus?: ConnectionStatusTone
  connectionLabel?: string
  connectionDetail?: string | null
  kanbanSnapshot?: KanbanBoardSnapshot | null
  processingActive?: boolean
  onClose?: () => void
  sidebarCollapsed?: boolean
}

function ConnectionBadge({ status, label }: { status: ConnectionStatusTone; label: string }) {
  const isConnected = status === 'connected'
  const isReconnecting = status === 'reconnecting' || status === 'connecting'

  return (
    <div className={`banner-connection banner-connection--${status}`}>
      <span className={`banner-connection-dot ${isReconnecting ? 'banner-connection-dot--pulse' : ''}`} />
      <span className="banner-connection-label">{label}</span>
      {isConnected && <Check size={10} className="banner-connection-check" strokeWidth={3} />}
    </div>
  )
}

export function AgentChatBanner({
  agentName,
  agentAvatarUrl,
  agentColorHex,
  connectionStatus = 'connecting',
  connectionLabel = 'Connecting',
  kanbanSnapshot,
  processingActive = false,
  onClose,
  sidebarCollapsed = true,
}: AgentChatBannerProps) {
  const trimmedName = agentName.trim() || 'Agent'
  const accentColor = normalizeHexColor(agentColorHex) || '#6366f1'
  const bannerRef = useRef<HTMLDivElement | null>(null)
  const [animate, setAnimate] = useState(false)
  const prevDoneRef = useRef<number | null>(null)
  const [justCompleted, setJustCompleted] = useState(false)

  useEffect(() => {
    const node = bannerRef.current
    if (!node || typeof window === 'undefined') return

    const updateHeight = () => {
      const height = node.getBoundingClientRect().height
      document.documentElement.style.setProperty('--agent-chat-banner-height', `${height}px`)
    }

    updateHeight()
    const observer = new ResizeObserver(updateHeight)
    observer.observe(node)

    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty('--agent-chat-banner-height')
    }
  }, [])

  // Animate on mount and when kanban changes
  useEffect(() => {
    if (kanbanSnapshot) {
      setAnimate(false)
      const timer = setTimeout(() => setAnimate(true), 30)
      return () => clearTimeout(timer)
    }
  }, [kanbanSnapshot?.doneCount, kanbanSnapshot?.todoCount, kanbanSnapshot?.doingCount])

  // Detect task completion for celebration
  useEffect(() => {
    if (kanbanSnapshot && prevDoneRef.current !== null && kanbanSnapshot.doneCount > prevDoneRef.current) {
      setJustCompleted(true)
      const timer = setTimeout(() => setJustCompleted(false), 1200)
      return () => clearTimeout(timer)
    }
    prevDoneRef.current = kanbanSnapshot?.doneCount ?? null
  }, [kanbanSnapshot?.doneCount])

  const hasKanban = kanbanSnapshot && (kanbanSnapshot.todoCount + kanbanSnapshot.doingCount + kanbanSnapshot.doneCount) > 0
  const totalTasks = hasKanban ? kanbanSnapshot.todoCount + kanbanSnapshot.doingCount + kanbanSnapshot.doneCount : 0
  const doneTasks = hasKanban ? kanbanSnapshot.doneCount : 0
  const currentTask = hasKanban && kanbanSnapshot.doingTitles.length > 0 ? kanbanSnapshot.doingTitles[0] : null
  const isAllComplete = hasKanban && doneTasks === totalTasks
  const percentage = totalTasks > 0 ? (doneTasks / totalTasks) * 100 : 0

  const shellClass = `banner-shell ${sidebarCollapsed ? 'banner-shell--sidebar-collapsed' : 'banner-shell--sidebar-expanded'}`

  return (
    <div className={shellClass} ref={bannerRef}>
      <div
        className={`banner ${hasKanban ? 'banner--with-progress' : ''} ${isAllComplete ? 'banner--complete' : ''} ${justCompleted ? 'banner--celebrating' : ''}`}
        style={{ '--banner-accent': accentColor } as React.CSSProperties}
      >
        {/* Left: Avatar + Info */}
        <div className="banner-left">
          <AgentAvatarBadge
            name={trimmedName}
            avatarUrl={agentAvatarUrl}
            className="banner-avatar"
            imageClassName="banner-avatar-image"
            textClassName="banner-avatar-text"
          />
          <div className="banner-info">
            <div className="banner-top-row">
              <span className="banner-name">{trimmedName}</span>
              <ConnectionBadge status={connectionStatus} label={connectionLabel} />
            </div>
            {hasKanban && currentTask ? (
              <div className={`banner-task ${animate ? 'banner-task--animate' : ''}`}>
                <span className={`banner-task-dot ${processingActive ? 'banner-task-dot--active' : ''}`} />
                <span className="banner-task-title">{currentTask}</span>
              </div>
            ) : hasKanban && isAllComplete ? (
              <div className="banner-task banner-task--complete">
                <Check size={12} className="banner-task-check" strokeWidth={2.5} />
                <span className="banner-task-title">All tasks complete</span>
              </div>
            ) : null}
          </div>
        </div>

        {/* Center: Progress */}
        {hasKanban ? (
          <div className={`banner-center ${animate ? 'banner-center--animate' : ''}`}>
            <div className="banner-progress-wrapper">
              <div className="banner-progress-bar">
                <div
                  className={`banner-progress-fill ${isAllComplete ? 'banner-progress-fill--complete' : ''} ${justCompleted ? 'banner-progress-fill--pop' : ''}`}
                  style={{
                    width: `${percentage}%`,
                    background: isAllComplete
                      ? 'linear-gradient(90deg, #10b981, #34d399)'
                      : `linear-gradient(90deg, ${accentColor}, color-mix(in srgb, ${accentColor} 70%, #a855f7))`,
                  }}
                />
              </div>
              <div className="banner-progress-count">
                <span className={`banner-progress-done ${justCompleted ? 'banner-progress-done--pop' : ''}`}>{doneTasks}</span>
                <span className="banner-progress-sep">/</span>
                <span className="banner-progress-total">{totalTasks}</span>
              </div>
            </div>
          </div>
        ) : null}

        {/* Right: Close button */}
        {onClose ? (
          <div className="banner-right">
            <button
              type="button"
              className="banner-close"
              onClick={onClose}
              aria-label="Close"
            >
              <X size={16} strokeWidth={1.75} />
            </button>
          </div>
        ) : null}

        {/* Celebration shimmer */}
        {justCompleted && <div className="banner-shimmer" aria-hidden="true" />}
      </div>
    </div>
  )
}
