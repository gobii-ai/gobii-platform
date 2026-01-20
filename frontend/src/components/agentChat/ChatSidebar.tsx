import { memo, useState, useCallback, useEffect, useMemo } from 'react'
import { PanelLeft, PanelLeftClose, Menu, Plus } from 'lucide-react'

import type { ConsoleContext } from '../../api/context'
import type { AgentRosterEntry } from '../../types/agentRoster'
import { AgentChatContextSwitcher, type AgentChatContextSwitcherData } from './AgentChatContextSwitcher'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import { AgentEmptyState, AgentListItem, AgentSearchInput } from './ChatSidebarParts'

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
  onCreateAgent?: () => void
  contextSwitcher?: AgentChatContextSwitcherData
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
  onCreateAgent,
  contextSwitcher,
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

  const handleCreateAgent = useCallback(() => {
    onCreateAgent?.()
    if (isMobile) {
      setDrawerOpen(false)
    }
  }, [isMobile, onCreateAgent])

  const hasAgents = agents.length > 0

  // Mobile FAB and drawer
  if (isMobile) {
    const mobileContextSwitcher = contextSwitcher
      ? {
          ...contextSwitcher,
          onSwitch: (context: ConsoleContext) => {
            void contextSwitcher.onSwitch(context)
            setDrawerOpen(false)
          },
        }
      : null

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

        <AgentChatMobileSheet
          open={drawerOpen}
          keepMounted={true}
          onClose={() => setDrawerOpen(false)}
          title="Switch agent"
          icon={PanelLeft}
          headerAccessory={mobileContextSwitcher ? (
            <AgentChatContextSwitcher {...mobileContextSwitcher} variant="drawer" />
          ) : null}
          ariaLabel="Switch agent"
        >
          {showSearch ? (
            <AgentSearchInput
              variant="drawer"
              value={searchQuery}
              onChange={setSearchQuery}
              onClear={() => setSearchQuery('')}
            />
          ) : null}
          <div className="agent-drawer-list" role="list">
            {onCreateAgent ? (
              <button
                type="button"
                className="chat-sidebar-create-btn chat-sidebar-create-btn--drawer"
                onClick={handleCreateAgent}
                aria-label="New agent"
              >
                <span className="chat-sidebar-create-btn-icon">
                  <Plus className="h-4 w-4" />
                </span>
                <span className="chat-sidebar-create-btn-label">New Agent</span>
              </button>
            ) : null}
            <AgentEmptyState
              variant="drawer"
              hasAgents={hasAgents}
              loading={loading}
              errorMessage={errorMessage}
              filteredCount={filteredAgents.length}
              searchQuery={searchQuery}
            />
            {filteredAgents.map((agent) => {
              const isActive = agent.id === activeAgentId
              const isSwitching = agent.id === switchingAgentId
              return (
                <AgentListItem
                  key={agent.id}
                  variant="drawer"
                  agent={agent}
                  isActive={isActive}
                  isSwitching={isSwitching}
                  onSelect={handleAgentSelect}
                  accentColor={agent.displayColorHex}
                />
              )
            })}
          </div>
        </AgentChatMobileSheet>
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
        <div className="chat-sidebar-controls" data-collapsed={collapsed ? 'true' : 'false'}>
          {contextSwitcher ? (
            <AgentChatContextSwitcher {...contextSwitcher} collapsed={collapsed} />
          ) : null}
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
        </div>

        <div className="chat-sidebar-section">
          <div className="chat-sidebar-section-header">
            <span className="chat-sidebar-section-title">Agents</span>
            {!collapsed && hasAgents ? (
              <span className="chat-sidebar-section-count">{agents.length}</span>
            ) : null}
          </div>

          {!collapsed && showSearch ? (
            <AgentSearchInput
              variant="sidebar"
              value={searchQuery}
              onChange={setSearchQuery}
              onClear={() => setSearchQuery('')}
            />
          ) : null}

          <div className="chat-sidebar-agent-list" role="list">
            {onCreateAgent ? (
              <button
                type="button"
                className="chat-sidebar-create-btn"
                onClick={handleCreateAgent}
                aria-label="New agent"
                data-collapsed={collapsed}
              >
                <span className="chat-sidebar-create-btn-icon">
                  <Plus className="h-4 w-4" />
                </span>
                {!collapsed ? (
                  <span className="chat-sidebar-create-btn-label">New Agent</span>
                ) : null}
              </button>
            ) : null}
            <AgentEmptyState
              variant="sidebar"
              hasAgents={hasAgents}
              loading={loading}
              errorMessage={errorMessage}
              filteredCount={filteredAgents.length}
              searchQuery={searchQuery}
            />
            {filteredAgents.map((agent) => {
              const isActive = agent.id === activeAgentId
              const isSwitching = agent.id === switchingAgentId
              return (
                <AgentListItem
                  key={agent.id}
                  variant="sidebar"
                  agent={agent}
                  isActive={isActive}
                  isSwitching={isSwitching}
                  onSelect={handleAgentSelect}
                  accentColor={agent.displayColorHex}
                  collapsed={collapsed}
                />
              )
            })}
          </div>
        </div>
      </div>
    </aside>
  )
})
