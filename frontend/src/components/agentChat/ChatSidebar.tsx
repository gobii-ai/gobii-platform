import { memo, useState, useCallback, useEffect, type CSSProperties } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'

import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import type { AgentRosterEntry } from '../../types/agentRoster'

type ChatSidebarProps = {
  agents?: AgentRosterEntry[]
  activeAgentId?: string | null
  switchingAgentId?: string | null
  loading?: boolean
  errorMessage?: string | null
  defaultCollapsed?: boolean
  onToggle?: (collapsed: boolean) => void
  onSelectAgent?: (agent: AgentRosterEntry) => void
}

export const ChatSidebar = memo(function ChatSidebar({
  agents = [],
  activeAgentId,
  switchingAgentId,
  loading = false,
  errorMessage,
  defaultCollapsed = true,
  onToggle,
  onSelectAgent,
}: ChatSidebarProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)
  const [isMobile, setIsMobile] = useState(false)

  // Detect mobile breakpoint
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  // On mobile, sidebar is hidden by default
  const handleToggle = useCallback(() => {
    const next = !collapsed
    setCollapsed(next)
    onToggle?.(next)
  }, [collapsed, onToggle])

  const hasAgents = agents.length > 0

  // Don't render sidebar on mobile (for now - can add drawer later)
  if (isMobile) {
    return null
  }

  return (
    <aside
      className={`chat-sidebar ${collapsed ? 'chat-sidebar--collapsed' : ''}`}
      data-collapsed={collapsed}
    >
      <div className="chat-sidebar-inner">
        {/* Toggle button */}
        <button
          type="button"
          className="chat-sidebar-toggle"
          onClick={handleToggle}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronLeft className="h-4 w-4" />
          )}
        </button>

        <div className="chat-sidebar-section">
          <div className="chat-sidebar-section-header">
            <span className="chat-sidebar-section-title">Agents</span>
            {!collapsed && hasAgents ? (
              <span className="chat-sidebar-section-count">{agents.length}</span>
            ) : null}
          </div>

          <div className="chat-sidebar-agent-list" role="list">
            {!hasAgents && loading ? (
              <div className="chat-sidebar-agent-empty">Loading agents...</div>
            ) : null}
            {!hasAgents && !loading && errorMessage ? (
              <div className="chat-sidebar-agent-empty">{errorMessage}</div>
            ) : null}
            {!hasAgents && !loading && !errorMessage ? (
              <div className="chat-sidebar-agent-empty">No agents yet.</div>
            ) : null}
            {agents.map((agent) => {
              const isActive = agent.id === activeAgentId
              const isSwitching = agent.id === switchingAgentId
              const accentStyle = agent.displayColorHex
                ? ({ '--agent-accent': agent.displayColorHex } as CSSProperties)
                : undefined
              return (
                <button
                  key={agent.id}
                  type="button"
                  className="chat-sidebar-agent"
                  data-active={isActive ? 'true' : 'false'}
                  data-switching={isSwitching ? 'true' : 'false'}
                  data-enabled={agent.isActive ? 'true' : 'false'}
                  onClick={() => onSelectAgent?.(agent)}
                  title={collapsed ? agent.name || 'Agent' : undefined}
                  style={accentStyle}
                  role="listitem"
                  aria-current={isActive ? 'page' : undefined}
                >
                  <AgentAvatarBadge
                    name={agent.name || 'Agent'}
                    avatarUrl={agent.avatarUrl}
                    className="chat-sidebar-agent-avatar"
                    imageClassName="chat-sidebar-agent-avatar-image"
                    textClassName="chat-sidebar-agent-avatar-text"
                  />
                  {!collapsed ? (
                    <span className="chat-sidebar-agent-meta">
                      <span className="chat-sidebar-agent-name">{agent.name || 'Agent'}</span>
                      {!agent.isActive ? (
                        <span className="chat-sidebar-agent-state">Paused</span>
                      ) : null}
                    </span>
                  ) : null}
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </aside>
  )
})
