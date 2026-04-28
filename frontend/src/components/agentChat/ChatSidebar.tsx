import { memo, useState, useCallback, useEffect, useMemo, type CSSProperties } from 'react'
import { ArrowLeftRight, LayoutGrid, List, PanelLeft, PanelLeftClose, PanelRightClose, Plus } from 'lucide-react'

import type { ConsoleContext } from '../../api/context'
import type { AgentRosterEntry, AgentRosterSortMode } from '../../types/agentRoster'
import { buildAgentSearchBlob } from '../../util/agentCards'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { AgentChatContextSwitcher, type AgentChatContextSwitcherData } from './AgentChatContextSwitcher'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import { ChatSidebarGallery } from './ChatSidebarGallery'
import { AgentEmptyState, AgentListItem, AgentListSectionHeader, AgentSearchInput, AgentSortToggle } from './ChatSidebarParts'
import { SidebarSettingsMenu, type SidebarSettingsInfo } from './SidebarSettingsMenu'
import {
  getNextAgentChatSidebarMode,
  getPreviousAgentChatSidebarMode,
  type AgentChatSidebarMode,
  SIDEBAR_MOBILE_BREAKPOINT_PX,
  type AgentDrawerViewMode,
} from './sidebarMode'

const SEARCH_THRESHOLD = 6

type ChatSidebarProps = {
  agents?: AgentRosterEntry[]
  favoriteAgentIds?: string[]
  activeAgentId?: string | null
  switchingAgentId?: string | null
  loading?: boolean
  errorMessage?: string | null
  desktopMode?: AgentChatSidebarMode
  onDesktopModeChange?: (mode: AgentChatSidebarMode) => void
  onSelectAgent?: (agent: AgentRosterEntry) => void
  onToggleAgentFavorite?: (agentId: string) => void
  onCreateAgent?: () => void
  createAgentDisabledReason?: string | null
  onBlockedCreateAgent?: (location: 'sidebar') => void
  rosterSortMode?: AgentRosterSortMode
  onRosterSortModeChange?: (mode: AgentRosterSortMode) => void
  contextSwitcher?: AgentChatContextSwitcherData
  settings?: SidebarSettingsInfo
}

export const ChatSidebar = memo(function ChatSidebar({
  agents = [],
  favoriteAgentIds = [],
  activeAgentId,
  switchingAgentId,
  loading = false,
  errorMessage,
  desktopMode = 'list',
  onDesktopModeChange,
  onSelectAgent,
  onToggleAgentFavorite,
  onCreateAgent,
  createAgentDisabledReason = null,
  onBlockedCreateAgent,
  rosterSortMode = 'recent',
  onRosterSortModeChange,
  contextSwitcher,
  settings,
}: ChatSidebarProps) {
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === 'undefined') {
      return false
    }
    return window.innerWidth < SIDEBAR_MOBILE_BREAKPOINT_PX
  })
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [drawerViewMode, setDrawerViewMode] = useState<AgentDrawerViewMode>('list')

  const collapsed = desktopMode === 'collapsed'
  const galleryMode = desktopMode === 'gallery'
  const showSearch = agents.length >= SEARCH_THRESHOLD
  const filteredAgents = useMemo(() => {
    if (!searchQuery.trim()) {
      return agents
    }
    const query = searchQuery.trim().toLowerCase()
    return agents.filter((agent) => buildAgentSearchBlob(agent).includes(query))
  }, [agents, searchQuery])

  const favoriteAgentIdSet = useMemo(() => new Set(favoriteAgentIds), [favoriteAgentIds])
  const hasFavoritesInRoster = useMemo(
    () => agents.some((agent) => favoriteAgentIdSet.has(agent.id)),
    [agents, favoriteAgentIdSet],
  )
  const favoriteFilteredAgents = useMemo(
    () => filteredAgents.filter((agent) => favoriteAgentIdSet.has(agent.id)),
    [filteredAgents, favoriteAgentIdSet],
  )
  const allFilteredAgents = useMemo(
    () => filteredAgents.filter((agent) => !favoriteAgentIdSet.has(agent.id)),
    [filteredAgents, favoriteAgentIdSet],
  )
  const collapsedFilteredAgents = useMemo(
    () => hasFavoritesInRoster ? [...favoriteFilteredAgents, ...allFilteredAgents] : filteredAgents,
    [allFilteredAgents, favoriteFilteredAgents, filteredAgents, hasFavoritesInRoster],
  )

  useEffect(() => {
    if (!drawerOpen) {
      setSearchQuery('')
    }
  }, [drawerOpen])

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < SIDEBAR_MOBILE_BREAKPOINT_PX)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  const handleStepLeft = useCallback(() => {
    onDesktopModeChange?.(getPreviousAgentChatSidebarMode(desktopMode))
  }, [desktopMode, onDesktopModeChange])

  const handleStepRight = useCallback(() => {
    onDesktopModeChange?.(getNextAgentChatSidebarMode(desktopMode))
  }, [desktopMode, onDesktopModeChange])

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
  const showSortToggle = agents.length >= 2
  const createAgentDisabled = Boolean(createAgentDisabledReason)
  const trackableCreateAgentDisabled = createAgentDisabled && Boolean(onBlockedCreateAgent)
  const createAgentButtonDisabled = createAgentDisabled && !trackableCreateAgentDisabled

  const handleCreateAgent = useCallback(() => {
    if (createAgentDisabled && onBlockedCreateAgent) {
      onBlockedCreateAgent('sidebar')
      return
    }
    onCreateAgent?.()
    if (isMobile) {
      setDrawerOpen(false)
    }
  }, [createAgentDisabled, isMobile, onBlockedCreateAgent, onCreateAgent])

  const fishCollateralEnabled = useMemo(() => {
    if (typeof document === 'undefined') {
      return true
    }
    const mountNode = document.getElementById('gobii-frontend-root')
    return mountNode?.dataset.fishCollateralEnabled !== 'false'
  }, [])
  const sidebarLogoSrc = fishCollateralEnabled ? '/static/images/gobii_fish.png' : '/static/images/noBgWhite.png'
  const sidebarLogoAlt = fishCollateralEnabled ? 'Gobii Fish' : 'Gobii'

  const activeAgent = useMemo(
    () => agents.find((agent) => agent.id === activeAgentId) ?? null,
    [agents, activeAgentId],
  )

  const renderListContent = useCallback((variant: 'drawer' | 'sidebar', collapsedView: boolean) => {
    const sourceAgents = collapsedView ? collapsedFilteredAgents : filteredAgents
    const emptyCount = collapsedView ? collapsedFilteredAgents.length : filteredAgents.length

    return (
      <>
        {onCreateAgent ? (
          <button
            type="button"
            className={`chat-sidebar-create-btn${variant === 'drawer' ? ' chat-sidebar-create-btn--drawer' : ''}`}
            onClick={handleCreateAgent}
            disabled={createAgentButtonDisabled}
            aria-disabled={createAgentDisabled ? 'true' : undefined}
            aria-label="New agent"
            data-collapsed={collapsedView}
            title={createAgentDisabledReason ?? undefined}
          >
            <span className="chat-sidebar-create-btn-icon">
              <Plus className="h-4 w-4" />
            </span>
            {variant === 'drawer' || !collapsedView ? (
              <span className="chat-sidebar-create-btn-label">New Agent</span>
            ) : null}
          </button>
        ) : null}

        <AgentEmptyState
          variant={variant}
          hasAgents={hasAgents}
          loading={loading}
          errorMessage={errorMessage}
          filteredCount={emptyCount}
          searchQuery={searchQuery}
        />

        {collapsedView ? (
          sourceAgents.map((agent) => (
            <AgentListItem
              key={agent.id}
              variant={variant}
              agent={agent}
              isActive={agent.id === activeAgentId}
              isSwitching={agent.id === switchingAgentId}
              isFavorite={favoriteAgentIdSet.has(agent.id)}
              onSelect={handleAgentSelect}
              onToggleFavorite={onToggleAgentFavorite}
              accentColor={agent.displayColorHex}
              collapsed={collapsedView}
              showFavoriteToggle={false}
            />
          ))
        ) : hasFavoritesInRoster ? (
          <>
            <AgentListSectionHeader
              variant={variant}
              label="Favorites"
              count={favoriteFilteredAgents.length}
            />
            {favoriteFilteredAgents.map((agent) => (
              <AgentListItem
                key={agent.id}
                variant={variant}
                agent={agent}
                isActive={agent.id === activeAgentId}
                isSwitching={agent.id === switchingAgentId}
                isFavorite={true}
                onSelect={handleAgentSelect}
                onToggleFavorite={onToggleAgentFavorite}
                accentColor={agent.displayColorHex}
                collapsed={collapsedView}
              />
            ))}
            <AgentListSectionHeader
              variant={variant}
              label="All agents"
              count={allFilteredAgents.length}
            />
            {allFilteredAgents.map((agent) => (
              <AgentListItem
                key={agent.id}
                variant={variant}
                agent={agent}
                isActive={agent.id === activeAgentId}
                isSwitching={agent.id === switchingAgentId}
                isFavorite={false}
                onSelect={handleAgentSelect}
                onToggleFavorite={onToggleAgentFavorite}
                accentColor={agent.displayColorHex}
                collapsed={collapsedView}
              />
            ))}
          </>
        ) : (
          sourceAgents.map((agent) => (
            <AgentListItem
              key={agent.id}
              variant={variant}
              agent={agent}
              isActive={agent.id === activeAgentId}
              isSwitching={agent.id === switchingAgentId}
              isFavorite={false}
              onSelect={handleAgentSelect}
              onToggleFavorite={onToggleAgentFavorite}
              accentColor={agent.displayColorHex}
              collapsed={collapsedView}
            />
          ))
        )}
      </>
    )
  }, [
    activeAgentId,
    allFilteredAgents,
    collapsedFilteredAgents,
    createAgentButtonDisabled,
    createAgentDisabled,
    createAgentDisabledReason,
    errorMessage,
    favoriteAgentIdSet,
    favoriteFilteredAgents,
    filteredAgents,
    handleAgentSelect,
    handleCreateAgent,
    hasAgents,
    hasFavoritesInRoster,
    loading,
    onCreateAgent,
    onToggleAgentFavorite,
    searchQuery,
    switchingAgentId,
  ])

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
    const fabAccent = activeAgent?.displayColorHex || '#6366f1'
    const fabStyle = { '--agent-fab-accent': fabAccent } as CSSProperties

    return (
      <>
        <button
          type="button"
          className="agent-fab"
          onClick={() => setDrawerOpen(true)}
          aria-label="Switch agent"
          aria-expanded={drawerOpen}
          style={fabStyle}
        >
          <AgentAvatarBadge
            name={activeAgent?.name || 'Agent'}
            avatarUrl={activeAgent?.avatarUrl}
            className="agent-fab-avatar"
            imageClassName="agent-fab-avatar-image"
            textClassName="agent-fab-avatar-text"
          />
          <span className="agent-fab-switch-badge" aria-hidden="true">
            <ArrowLeftRight className="h-2.5 w-2.5" />
          </span>
        </button>

        <AgentChatMobileSheet
          open={drawerOpen}
          keepMounted={true}
          onClose={() => setDrawerOpen(false)}
          title="Switch agent"
          icon={PanelLeft}
          bodyPadding={false}
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
          {showSortToggle ? (
            <AgentSortToggle
              variant="drawer"
              value={rosterSortMode}
              onChange={(mode) => onRosterSortModeChange?.(mode)}
            />
          ) : null}
          {hasAgents ? (
            <div className="agent-drawer-view-toggle" role="group" aria-label="Agent roster view">
              <button
                type="button"
                className="agent-drawer-view-toggle-button"
                data-active={drawerViewMode === 'list' ? 'true' : 'false'}
                onClick={() => setDrawerViewMode('list')}
              >
                <List className="h-4 w-4" />
                <span>List</span>
              </button>
              <button
                type="button"
                className="agent-drawer-view-toggle-button"
                data-active={drawerViewMode === 'gallery' ? 'true' : 'false'}
                onClick={() => setDrawerViewMode('gallery')}
              >
                <LayoutGrid className="h-4 w-4" />
                <span>Grid</span>
              </button>
            </div>
          ) : null}

          {drawerViewMode === 'gallery' ? (
            <ChatSidebarGallery
              variant="drawer"
              agents={filteredAgents}
              favoriteAgentIds={favoriteAgentIds}
              activeAgentId={activeAgentId}
              switchingAgentId={switchingAgentId}
              hasAgents={hasAgents}
              loading={loading}
              errorMessage={errorMessage}
              searchQuery={searchQuery}
              onSelectAgent={handleAgentSelect}
              onToggleAgentFavorite={onToggleAgentFavorite}
              onCreateAgent={onCreateAgent ? handleCreateAgent : undefined}
              createAgentButtonDisabled={createAgentButtonDisabled}
              createAgentDisabledReason={createAgentDisabledReason}
            />
          ) : (
            <div className="agent-drawer-list" role="list">
              {renderListContent('drawer', false)}
            </div>
          )}
          {settings ? <SidebarSettingsMenu {...settings} variant="drawer" /> : null}
        </AgentChatMobileSheet>
      </>
    )
  }

  return (
    <aside
      className={`chat-sidebar chat-sidebar--${desktopMode}`}
      data-collapsed={collapsed}
      data-sidebar-mode={desktopMode}
    >
      <div className="chat-sidebar-inner">
        <div className="chat-sidebar-header" data-collapsed={collapsed ? 'true' : 'false'}>
          {!collapsed ? (
            <a href="/" className="chat-sidebar-logo-link">
              <img src={sidebarLogoSrc} alt={sidebarLogoAlt} className="chat-sidebar-logo" />
            </a>
          ) : null}
          <div className="chat-sidebar-header-actions">
            {contextSwitcher ? (
              <AgentChatContextSwitcher {...contextSwitcher} collapsed={collapsed} />
            ) : null}
            {!collapsed ? (
              <button
                type="button"
                className="chat-sidebar-toggle"
                onClick={handleStepLeft}
                aria-label={galleryMode ? 'Show list view' : 'Collapse sidebar'}
                title={galleryMode ? 'Show list view' : 'Collapse sidebar'}
              >
                <PanelLeftClose className="h-4 w-4" />
              </button>
            ) : null}
            {!galleryMode ? (
              <button
                type="button"
                className="chat-sidebar-toggle"
                onClick={handleStepRight}
                aria-label={collapsed ? 'Expand sidebar' : 'Expand agent gallery'}
                title={collapsed ? 'Expand sidebar' : 'Expand agent gallery'}
              >
                <PanelRightClose className="h-4 w-4" />
              </button>
            ) : null}
          </div>
        </div>

        <div className="chat-sidebar-section">
          <div className="chat-sidebar-section-header">
            <span className="chat-sidebar-section-title">Agents</span>
            {!collapsed && hasAgents ? (
              <span className="chat-sidebar-section-count">{agents.length}</span>
            ) : null}
          </div>

          {!collapsed && (showSearch || showSortToggle) ? (
            <div
              className="chat-sidebar-controls"
              data-gallery={galleryMode ? 'true' : 'false'}
            >
              {showSearch ? (
                <AgentSearchInput
                  variant="sidebar"
                  value={searchQuery}
                  onChange={setSearchQuery}
                  onClear={() => setSearchQuery('')}
                />
              ) : null}
              {showSortToggle ? (
                <AgentSortToggle
                  variant="sidebar"
                  value={rosterSortMode}
                  onChange={(mode) => onRosterSortModeChange?.(mode)}
                />
              ) : null}
            </div>
          ) : null}

          {galleryMode ? (
            <ChatSidebarGallery
              variant="sidebar"
              agents={filteredAgents}
              favoriteAgentIds={favoriteAgentIds}
              activeAgentId={activeAgentId}
              switchingAgentId={switchingAgentId}
              hasAgents={hasAgents}
              loading={loading}
              errorMessage={errorMessage}
              searchQuery={searchQuery}
              onSelectAgent={handleAgentSelect}
              onToggleAgentFavorite={onToggleAgentFavorite}
              onCreateAgent={onCreateAgent ? handleCreateAgent : undefined}
              createAgentButtonDisabled={createAgentButtonDisabled}
              createAgentDisabledReason={createAgentDisabledReason}
            />
          ) : (
            <div className="chat-sidebar-agent-list" role="list">
              {renderListContent('sidebar', collapsed)}
            </div>
          )}
        </div>

        {settings ? (
          <SidebarSettingsMenu
            {...settings}
            variant="sidebar"
            collapsed={collapsed}
          />
        ) : null}
      </div>
    </aside>
  )
})
