import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Circle, Loader2 } from 'lucide-react'

import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { ConnectionStatusIndicator, type ConnectionStatusTone } from './ConnectionStatusIndicator'
import { normalizeHexColor } from '../../util/color'
import type { KanbanBoardSnapshot } from '../../types/agentChat'

type AgentChatBannerProps = {
  agentName: string
  agentAvatarUrl?: string | null
  agentColorHex?: string | null
  connectionStatus?: ConnectionStatusTone
  connectionLabel?: string
  connectionDetail?: string | null
  kanbanSnapshot?: KanbanBoardSnapshot | null
  processingActive?: boolean
}

function ProgressBar({ done, total, accentColor }: { done: number; total: number; accentColor: string }) {
  const percentage = total > 0 ? (done / total) * 100 : 0
  const isComplete = done === total && total > 0

  return (
    <div className="banner-progress">
      <div className="banner-progress-track">
        <div
          className={`banner-progress-fill ${isComplete ? 'banner-progress-fill--complete' : ''}`}
          style={{
            width: `${percentage}%`,
            background: isComplete
              ? 'linear-gradient(90deg, #10b981, #34d399)'
              : `linear-gradient(90deg, ${accentColor}, color-mix(in srgb, ${accentColor} 70%, #a855f7))`,
          }}
        />
      </div>
      <div className="banner-progress-label">
        <span className="banner-progress-count">{done}</span>
        <span className="banner-progress-divider">/</span>
        <span className="banner-progress-total">{total}</span>
      </div>
    </div>
  )
}

function CurrentTask({ title, isProcessing }: { title: string; isProcessing: boolean }) {
  return (
    <div className="banner-current-task">
      <div className="banner-task-indicator">
        {isProcessing ? (
          <Loader2 size={12} className="banner-task-spinner" />
        ) : (
          <Circle size={10} className="banner-task-dot" />
        )}
      </div>
      <span className="banner-task-label">Working on:</span>
      <span className="banner-task-title">{title}</span>
    </div>
  )
}

export function AgentChatBanner({
  agentName,
  agentAvatarUrl,
  agentColorHex,
  connectionStatus,
  connectionLabel,
  connectionDetail,
  kanbanSnapshot,
  processingActive = false,
}: AgentChatBannerProps) {
  const trimmedName = agentName.trim() || 'Agent'
  const accentColor = normalizeHexColor(agentColorHex) || '#6366f1'
  const bannerRef = useRef<HTMLDivElement | null>(null)
  const [animate, setAnimate] = useState(false)

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

  // Animate progress changes
  useEffect(() => {
    if (kanbanSnapshot) {
      setAnimate(false)
      const timer = setTimeout(() => setAnimate(true), 50)
      return () => clearTimeout(timer)
    }
  }, [kanbanSnapshot?.doneCount, kanbanSnapshot?.todoCount, kanbanSnapshot?.doingCount])

  const hasKanban = kanbanSnapshot && (kanbanSnapshot.todoCount + kanbanSnapshot.doingCount + kanbanSnapshot.doneCount) > 0
  const totalTasks = hasKanban ? kanbanSnapshot.todoCount + kanbanSnapshot.doingCount + kanbanSnapshot.doneCount : 0
  const doneTasks = hasKanban ? kanbanSnapshot.doneCount : 0
  const currentTask = hasKanban && kanbanSnapshot.doingTitles.length > 0 ? kanbanSnapshot.doingTitles[0] : null
  const isComplete = hasKanban && doneTasks === totalTasks

  return (
    <div className="fixed inset-x-0 top-0 z-30">
      <div className="mx-auto w-full max-w-5xl px-4 pb-3 pt-4 sm:px-6 lg:px-10" ref={bannerRef}>
        <div
          className={`banner-surface ${hasKanban ? 'banner-surface--with-kanban' : ''} ${isComplete ? 'banner-surface--complete' : ''}`}
          style={{ '--banner-accent': accentColor } as React.CSSProperties}
        >
          {/* Main row: Avatar + Name + Connection */}
          <div className="banner-main-row">
            <div className="banner-identity">
              <AgentAvatarBadge
                name={trimmedName}
                avatarUrl={agentAvatarUrl}
                className="banner-avatar"
                imageClassName="h-full w-full object-cover"
                textClassName="flex h-full w-full items-center justify-center text-lg font-semibold text-white"
                style={{ borderColor: accentColor }}
                fallbackStyle={{ background: `linear-gradient(135deg, ${accentColor}, #0f172a)` }}
              />
              <div className="banner-info">
                <div className="banner-meta">
                  <span className="banner-meta-label">Live chat</span>
                  {connectionStatus && connectionLabel ? (
                    <ConnectionStatusIndicator
                      status={connectionStatus}
                      label={connectionLabel}
                      detail={connectionDetail}
                    />
                  ) : null}
                </div>
                <div className="banner-name">{trimmedName}</div>
              </div>
            </div>

            {/* Progress ring for kanban - desktop */}
            {hasKanban ? (
              <div className={`banner-progress-ring ${animate ? 'banner-progress-ring--animate' : ''} ${isComplete ? 'banner-progress-ring--complete' : ''}`}>
                <svg viewBox="0 0 36 36" className="banner-ring-svg">
                  <circle
                    cx="18"
                    cy="18"
                    r="15.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3"
                    className="banner-ring-track"
                  />
                  <circle
                    cx="18"
                    cy="18"
                    r="15.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeDasharray={2 * Math.PI * 15.5}
                    strokeDashoffset={animate ? 2 * Math.PI * 15.5 * (1 - doneTasks / totalTasks) : 2 * Math.PI * 15.5}
                    className="banner-ring-progress"
                    style={{ stroke: isComplete ? '#10b981' : accentColor }}
                    transform="rotate(-90 18 18)"
                  />
                </svg>
                {isComplete ? (
                  <CheckCircle2 size={18} className="banner-ring-check" />
                ) : (
                  <div className="banner-ring-content">
                    <span className="banner-ring-done">{doneTasks}</span>
                    <span className="banner-ring-total">/{totalTasks}</span>
                  </div>
                )}
              </div>
            ) : null}
          </div>

          {/* Kanban progress row - shown when kanban is active */}
          {hasKanban ? (
            <div className={`banner-kanban-row ${animate ? 'banner-kanban-row--animate' : ''}`}>
              {currentTask ? (
                <CurrentTask title={currentTask} isProcessing={processingActive} />
              ) : isComplete ? (
                <div className="banner-complete-message">
                  <CheckCircle2 size={14} className="banner-complete-icon" />
                  <span>All tasks complete</span>
                </div>
              ) : (
                <div className="banner-idle-message">Ready for next task</div>
              )}
              <ProgressBar done={doneTasks} total={totalTasks} accentColor={accentColor} />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
