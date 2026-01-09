import { memo, useState, useCallback, useEffect, useMemo, type CSSProperties } from 'react'
import { PanelLeft, PanelLeftClose, X, Check, Menu, Search } from 'lucide-react'

import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import type { AgentRosterEntry } from '../../types/agentRoster'

const SEARCH_THRESHOLD = 6

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
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  const showSearch = agents.length >= SEARCH_THRESHOLD
  const filteredAgents = useMemo(() => {
    if (!searchQuery.trim()) return agents
    const query = searchQuery.toLowerCase()
    return agents.filter(
      (agent) =>
        agent.name?.toLowerCase().includes(query) ||
        agent.shortDescription?.toLowerCase().includes(query),
    )
  }, [agents, searchQuery])

  // Clear search when drawer closes
  useEffect(() => {
    if (!drawerOpen) {
      setSearchQuery('')
    }
  }, [drawerOpen])

  // Detect mobile breakpoint
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  // Close drawer on escape key
  useEffect(() => {
    if (!drawerOpen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDrawerOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [drawerOpen])

  // Prevent body scroll when drawer is open
  useEffect(() => {
    if (drawerOpen) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => {
      document.body.style.overflow = ''
    }
  }, [drawerOpen])

  const handleToggle = useCallback(() => {
    const next = !collapsed
    setCollapsed(next)
    onToggle?.(next)
  }, [collapsed, onToggle])

  const handleAgentSelect = useCallback(
    (agent: AgentRosterEntry) => {
      onSelectAgent?.(agent)
      if (isMobile) {
        setDrawerOpen(false)
      }
    },
    [isMobile, onSelectAgent],
  )

  const hasAgents = agents.length > 0
  const activeAgent = agents.find((a) => a.id === activeAgentId)

  // Mobile FAB and drawer
  if (isMobile) {
    return (
      <>
        {/* FAB button */}
        <button
          type="button"
          className="agent-fab"
          onClick={() => setDrawerOpen(true)}
          aria-label="Open menu"
          aria-expanded={drawerOpen}
        >
          <Menu className="h-5 w-5" />
        </button>

        {/* Drawer backdrop */}
        <div
          className={`agent-drawer-backdrop ${drawerOpen ? 'agent-drawer-backdrop--open' : ''}`}
          onClick={() => setDrawerOpen(false)}
          aria-hidden="true"
        />

        {/* Drawer */}
        <div
          className={`agent-drawer ${drawerOpen ? 'agent-drawer--open' : ''}`}
          role="dialog"
          aria-modal="true"
          aria-label="Switch agent"
        >
          <div className="agent-drawer-header">
            <span className="agent-drawer-title">Switch Agent</span>
            <button
              type="button"
              className="agent-drawer-close"
              onClick={() => setDrawerOpen(false)}
              aria-label="Close"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
          {showSearch ? (
            <div className="agent-drawer-search">
              <Search className="agent-drawer-search-icon" aria-hidden="true" />
              <input
                type="text"
                className="agent-drawer-search-input"
                placeholder="Search agents..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
              />
              {searchQuery ? (
                <button
                  type="button"
                  className="agent-drawer-search-clear"
                  onClick={() => setSearchQuery('')}
                  aria-label="Clear search"
                >
                  <X className="h-4 w-4" />
                </button>
              ) : null}
            </div>
          ) : null}
          <div className="agent-drawer-list" role="list">
            {!hasAgents && loading ? (
              <div className="agent-drawer-empty">Loading agents...</div>
            ) : null}
            {!hasAgents && !loading && errorMessage ? (
              <div className="agent-drawer-empty">{errorMessage}</div>
            ) : null}
            {!hasAgents && !loading && !errorMessage ? (
              <div className="agent-drawer-empty">No agents yet.</div>
            ) : null}
            {hasAgents && filteredAgents.length === 0 && searchQuery ? (
              <div className="agent-drawer-empty">No agents match "{searchQuery}"</div>
            ) : null}
            {filteredAgents.map((agent) => {
              const isActive = agent.id === activeAgentId
              const isSwitching = agent.id === switchingAgentId
              const accentStyle = agent.displayColorHex
                ? ({ '--agent-accent': agent.displayColorHex } as CSSProperties)
                : undefined
              return (
                <button
                  key={agent.id}
                  type="button"
                  className="agent-drawer-item"
                  data-active={isActive ? 'true' : 'false'}
                  data-switching={isSwitching ? 'true' : 'false'}
                  data-enabled={agent.isActive ? 'true' : 'false'}
                  onClick={() => handleAgentSelect(agent)}
                  style={accentStyle}
                  role="listitem"
                  aria-current={isActive ? 'page' : undefined}
                >
                  <AgentAvatarBadge
                    name={agent.name || 'Agent'}
                    avatarUrl={agent.avatarUrl}
                    className="agent-drawer-item-avatar"
                    imageClassName="agent-drawer-item-avatar-image"
                    textClassName="agent-drawer-item-avatar-text"
                  />
                  <span className="agent-drawer-item-meta">
                    <span className="agent-drawer-item-name">{agent.name || 'Agent'}</span>
                    {agent.shortDescription ? (
                      <span className="agent-drawer-item-desc">{agent.shortDescription}</span>
                    ) : !agent.isActive ? (
                      <span className="agent-drawer-item-state">Paused</span>
                    ) : null}
                  </span>
                  {isActive ? (
                    <Check className="agent-drawer-item-check" aria-hidden="true" />
                  ) : null}
                </button>
              )
            })}
          </div>
        </div>
      </>
    )
  }

  // Desktop sidebar
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
            <PanelLeft className="h-4 w-4" />
          ) : (
            <PanelLeftClose className="h-4 w-4" />
          )}
        </button>

        <div className="chat-sidebar-section">
          <div className="chat-sidebar-section-header">
            <span className="chat-sidebar-section-title">Agents</span>
            {!collapsed && hasAgents ? (
              <span className="chat-sidebar-section-count">{agents.length}</span>
            ) : null}
          </div>

          {!collapsed && showSearch ? (
            <div className="chat-sidebar-search">
              <Search className="chat-sidebar-search-icon" aria-hidden="true" />
              <input
                type="text"
                className="chat-sidebar-search-input"
                placeholder="Search..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
              />
              {searchQuery ? (
                <button
                  type="button"
                  className="chat-sidebar-search-clear"
                  onClick={() => setSearchQuery('')}
                  aria-label="Clear search"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </div>
          ) : null}

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
            {hasAgents && filteredAgents.length === 0 && searchQuery ? (
              <div className="chat-sidebar-agent-empty">No matches</div>
            ) : null}
            {filteredAgents.map((agent) => {
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
                  onClick={() => handleAgentSelect(agent)}
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
                      {agent.shortDescription ? (
                        <span className="chat-sidebar-agent-desc">{agent.shortDescription}</span>
                      ) : !agent.isActive ? (
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
