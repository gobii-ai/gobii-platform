import { memo, useState, useCallback, useEffect, useMemo, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { ArrowLeftRight, Bell, BellOff, Check, LayoutGrid, List, PanelLeftClose, PanelRightClose, Plus, Search, Settings, X } from 'lucide-react'

import type { ConsoleContext } from '../../api/context'
import { useAppSelector } from '../../store/hooks'
import { selectActiveChatAgentId } from '../../store/chatSlice'
import type { AgentRosterEntry, AgentRosterSortMode, AgentSidebarInvite } from '../../types/agentRoster'
import { ActionConfirmDialog } from '../common/ActionConfirmDialog'
import { FixedContextMenu, type FixedContextMenuPosition } from '../common/FixedContextMenu'
import { AgentCreateSplitButton, type TeamTemplateCreateMenu } from './AgentCreateSplitButton'
import { AgentChatContextSwitcher, type AgentChatContextSwitcherData } from './AgentChatContextSwitcher'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import { ChatSidebarGallery } from './ChatSidebarGallery'
import { SelectionShellPageSwitcher, SELECTION_SHELL_PAGE_LABELS, type SelectionShellPage } from './SelectionShellPageSwitcher'
import { AgentEmptyState, AgentListItem, AgentListSectionHeader, AgentSearchInput, AgentSortToggle } from './ChatSidebarParts'
import { ProductAnnouncementBell } from './ProductAnnouncementBell'
import { MessageSearchPanel, type MessageSearchState } from './MessageSearchPanel'
import { SidebarSettingsMenu, type SidebarSettingsInfo } from './SidebarSettingsMenu'
import { AgentInviteDetails, AgentInviteSidebarItem, type AgentInviteAction, type AgentInviteDialogState } from './AgentInviteSidebarItem'
import { getNextAgentChatSidebarMode, getPreviousAgentChatSidebarMode, type AgentChatSidebarMode, SIDEBAR_MOBILE_BREAKPOINT_PX, type AgentDrawerViewMode } from './sidebarMode'
import { useMobileDrawerHistory } from './useMobileDrawerHistory'
import { AgentChatAvatar, AgentChatButton } from './uiPrimitives'
import { VirtualizedRosterSurface, type VirtualRosterRow } from './VirtualizedRosterSurface'

type AgentContextMenuState = FixedContextMenuPosition & {
  agent: AgentRosterEntry
}

export type { MessageSearchState } from './MessageSearchPanel'

export type ChatSidebarProps = {
  agents?: AgentRosterEntry[]
  agentInvites?: AgentSidebarInvite[]
  favoriteAgentIds?: string[]
  mutedAgentIds?: string[]
  activeAgentId?: string | null
  switchingAgentId?: string | null
  loading?: boolean
  errorMessage?: string | null
  desktopMode?: AgentChatSidebarMode
  onDesktopModeChange?: (mode: AgentChatSidebarMode) => void
  onSelectAgent?: (agent: AgentRosterEntry, messageId?: string) => void
  onRespondInvite?: (invite: AgentSidebarInvite, action: AgentInviteAction) => Promise<void>
  onConfigureAgent?: (agent: AgentRosterEntry) => void
  onToggleAgentFavorite?: (agentId: string) => void
  onToggleAgentMute?: (agentId: string) => void
  onCreateAgent?: () => void
  createAgentDisabledReason?: string | null
  onBlockedCreateAgent?: (location: 'sidebar') => void
  teamTemplateMenu?: TeamTemplateCreateMenu | null
  rosterSortMode?: AgentRosterSortMode
  onRosterSortModeChange?: (mode: AgentRosterSortMode) => void
  contextSwitcher?: AgentChatContextSwitcherData
  settings?: SidebarSettingsInfo
  galleryShellPage?: SelectionShellPage
  galleryShellPanel?: ReactNode
  onGalleryShellPageChange?: (page: SelectionShellPage) => void
  showEmbeddedSettings?: boolean
  embeddedSettingsPanel?: ReactNode
  embeddedSettingsTitle?: string
  onBackFromEmbeddedSettings?: () => void
  scrollToAgentId?: string | null
  onScrolledToAgent?: (agentId: string) => void
  messageSearchState?: MessageSearchState
  onMessageSearchStateChange?: Dispatch<SetStateAction<MessageSearchState>>
}

export const ChatSidebar = memo(function ChatSidebar({
  agents = [],
  agentInvites = [],
  favoriteAgentIds = [],
  mutedAgentIds = [],
  activeAgentId: activeAgentIdOverride,
  switchingAgentId,
  loading = false,
  errorMessage,
  desktopMode = 'list',
  onDesktopModeChange,
  onSelectAgent,
  onRespondInvite,
  onConfigureAgent,
  onToggleAgentFavorite,
  onToggleAgentMute,
  onCreateAgent,
  createAgentDisabledReason = null,
  onBlockedCreateAgent,
  teamTemplateMenu = null,
  rosterSortMode = 'recent',
  onRosterSortModeChange,
  contextSwitcher,
  settings,
  galleryShellPage = 'agents',
  galleryShellPanel = null,
  onGalleryShellPageChange,
  showEmbeddedSettings = false,
  embeddedSettingsPanel = null,
  embeddedSettingsTitle = 'Agent Settings',
  onBackFromEmbeddedSettings,
  scrollToAgentId = null,
  onScrolledToAgent,
  messageSearchState: controlledMessageSearchState,
  onMessageSearchStateChange,
}: ChatSidebarProps) {
  const storeActiveAgentId = useAppSelector(selectActiveChatAgentId)
  const activeAgentId = activeAgentIdOverride !== undefined ? activeAgentIdOverride : storeActiveAgentId
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === 'undefined') {
      return false
    }
    return window.innerWidth < SIDEBAR_MOBILE_BREAKPOINT_PX
  })
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [localMessageSearchState, setLocalMessageSearchState] = useState<MessageSearchState>({
    open: false,
    query: '',
    submittedQuery: null,
  })
  const messageSearchState = controlledMessageSearchState ?? localMessageSearchState
  const updateMessageSearchState = onMessageSearchStateChange ?? setLocalMessageSearchState
  const messageSearchOpen = messageSearchState.open
  const searchQuery = messageSearchState.query
  const setMessageSearchOpen = useCallback((open: boolean) => {
    updateMessageSearchState((current) => (
      current.open === open ? current : { ...current, open }
    ))
  }, [updateMessageSearchState])
  const setSearchQuery = useCallback((query: string) => {
    updateMessageSearchState((current) => (
      current.query === query ? current : { ...current, query }
    ))
  }, [updateMessageSearchState])
  const [drawerViewMode, setDrawerViewMode] = useState<AgentDrawerViewMode>('list')
  const closeDrawerFromHistory = useCallback(() => setDrawerOpen(false), [])
  useMobileDrawerHistory(drawerOpen, isMobile, closeDrawerFromHistory)
  const [agentContextMenu, setAgentContextMenu] = useState<AgentContextMenuState | null>(null)
  const [inviteDialog, setInviteDialog] = useState<AgentInviteDialogState | null>(null)
  const [inviteBusy, setInviteBusy] = useState(false)
  const [inviteError, setInviteError] = useState<string | null>(null)

  const showSettingsView = showEmbeddedSettings && Boolean(embeddedSettingsPanel)
  const showGalleryShellSwitcher = Boolean(onGalleryShellPageChange)
  const showCustomGalleryShellPanel = galleryShellPage !== 'agents' && Boolean(galleryShellPanel)
  const collapsed = desktopMode === 'collapsed' && !showSettingsView
  const galleryMode = desktopMode === 'gallery' || showSettingsView
  const favoriteAgentIdSet = useMemo(() => new Set(favoriteAgentIds), [favoriteAgentIds])
  const mutedAgentIdSet = useMemo(() => new Set(mutedAgentIds), [mutedAgentIds])
  const hasFavoritesInRoster = useMemo(
    () => agents.some((agent) => favoriteAgentIdSet.has(agent.id)),
    [agents, favoriteAgentIdSet],
  )
  const favoriteFilteredAgents = useMemo(
    () => agents.filter((agent) => favoriteAgentIdSet.has(agent.id)),
    [agents, favoriteAgentIdSet],
  )
  const allFilteredAgents = useMemo(
    () => agents.filter((agent) => !favoriteAgentIdSet.has(agent.id)),
    [agents, favoriteAgentIdSet],
  )
  const collapsedFilteredAgents = useMemo(
    () => hasFavoritesInRoster ? [...favoriteFilteredAgents, ...allFilteredAgents] : agents,
    [agents, allFilteredAgents, favoriteFilteredAgents, hasFavoritesInRoster],
  )

  useEffect(() => {
    if (isMobile && !drawerOpen) {
      updateMessageSearchState((current) => (
        !current.open && !current.query && !current.submittedQuery
          ? current
          : { open: false, query: '', submittedQuery: null }
      ))
    }
  }, [drawerOpen, isMobile, updateMessageSearchState])

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < SIDEBAR_MOBILE_BREAKPOINT_PX)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  useEffect(() => {
    if (showSettingsView) {
      setDrawerOpen(true)
      setDrawerViewMode('gallery')
    }
  }, [showSettingsView])

  useEffect(() => {
    if (showCustomGalleryShellPanel) {
      setDrawerViewMode('gallery')
      if (isMobile) {
        setDrawerOpen(true)
      }
    }
  }, [isMobile, showCustomGalleryShellPanel, galleryShellPage])

  const handleStepLeft = useCallback(() => {
    if (showSettingsView) {
      onDesktopModeChange?.('list')
      return
    }
    onDesktopModeChange?.(getPreviousAgentChatSidebarMode(desktopMode))
  }, [desktopMode, onDesktopModeChange, showSettingsView])

  const handleStepRight = useCallback(() => {
    if (showSettingsView) {
      return
    }
    onDesktopModeChange?.(getNextAgentChatSidebarMode(desktopMode))
  }, [desktopMode, onDesktopModeChange, showSettingsView])

  const openMessageSearch = useCallback(() => {
    if (isMobile) {
      setDrawerOpen(true)
    } else {
      onDesktopModeChange?.('list')
    }
    setMessageSearchOpen(true)
  }, [isMobile, onDesktopModeChange, setMessageSearchOpen])

  const openAndFocusMessageSearch = useCallback(() => {
    openMessageSearch()
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLInputElement>(
        '.message-search-panel .agent-roster-search__input',
      )?.focus()
    })
  }, [openMessageSearch])

  useEffect(() => {
    const handleFindShortcut = (event: globalThis.KeyboardEvent) => {
      if (
        event.defaultPrevented
        || event.key.toLocaleLowerCase() !== 'f'
        || (!event.metaKey && !event.ctrlKey)
        || event.altKey
        || event.shiftKey
      ) {
        return
      }
      event.preventDefault()
      openAndFocusMessageSearch()
    }

    document.addEventListener('keydown', handleFindShortcut, true)
    return () => document.removeEventListener('keydown', handleFindShortcut, true)
  }, [openAndFocusMessageSearch])

  const handleAgentSelect = useCallback(
    (agent: AgentRosterEntry, messageId?: string) => {
      onSelectAgent?.(agent, messageId)
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

  const openInviteDialog = useCallback((invite: AgentSidebarInvite, action: AgentInviteAction) => {
    setInviteError(null)
    setInviteDialog({ invite, action })
  }, [])

  const closeInviteDialog = useCallback(() => {
    if (inviteBusy) {
      return
    }
    setInviteDialog(null)
    setInviteError(null)
  }, [inviteBusy])

  const handleConfirmInvite = useCallback(async () => {
    if (!inviteDialog || !onRespondInvite) {
      return
    }
    setInviteBusy(true)
    setInviteError(null)
    try {
      await onRespondInvite(inviteDialog.invite, inviteDialog.action)
      setInviteDialog(null)
      if (isMobile && inviteDialog.action === 'accept') {
        setDrawerOpen(false)
      }
    } catch (error) {
      setInviteError(error instanceof Error ? error.message : 'Could not respond to the invite.')
    } finally {
      setInviteBusy(false)
    }
  }, [inviteDialog, isMobile, onRespondInvite])

  const closeAgentContextMenu = useCallback(() => {
    setAgentContextMenu(null)
  }, [])

  const openAgentContextMenu = useCallback((agent: AgentRosterEntry, position: FixedContextMenuPosition) => {
    setAgentContextMenu({ agent, ...position })
  }, [])

  const sidebarLogoSrc = '/static/images/gobii_fish.png'
  const sidebarLogoAlt = 'Gobii Fish'

  const activeAgent = useMemo(
    () => agents.find((agent) => agent.id === activeAgentId) ?? null,
    [agents, activeAgentId],
  )

  const shellTitle = SELECTION_SHELL_PAGE_LABELS[galleryShellPage] ?? 'Agents'
  const showHeaderPageSwitcher = !collapsed && showGalleryShellSwitcher && galleryMode
  const showHeaderContextSwitcher = !collapsed && Boolean(contextSwitcher)
  const showHeaderCenter = showHeaderPageSwitcher || showHeaderContextSwitcher
  const showOrganizationShellPage = settings?.context ? settings.context.type === 'organization' : true
  const contextMenuMuted = agentContextMenu ? mutedAgentIdSet.has(agentContextMenu.agent.id) : false
  const agentContextMenuElement = agentContextMenu ? (
    <FixedContextMenu
      position={agentContextMenu}
      onClose={closeAgentContextMenu}
      ariaLabel={`${agentContextMenu.agent.name || 'Agent'} actions`}
      items={[
        {
          label: contextMenuMuted ? 'Unmute' : 'Mute',
          icon: contextMenuMuted ? Bell : BellOff,
          disabled: !onToggleAgentMute,
          onSelect: () => onToggleAgentMute?.(agentContextMenu.agent.id),
        },
        {
          label: 'Settings',
          icon: Settings,
          disabled: !onConfigureAgent,
          onSelect: () => {
            onConfigureAgent?.(agentContextMenu.agent)
            if (isMobile) setDrawerOpen(false)
          },
        },
      ]}
    />
  ) : null
  const inviteDialogElement = inviteDialog ? (
    <ActionConfirmDialog
      open={true}
      title={inviteDialog.invite.kind === 'transfer'
        ? `${inviteDialog.action === 'accept' ? 'Accept' : 'Decline'} transfer for ${inviteDialog.invite.agent_name || 'this agent'}?`
        : inviteDialog.action === 'accept'
          ? `Collaborate on ${inviteDialog.invite.agent_name || 'this agent'}?`
          : `Decline collaboration invite for ${inviteDialog.invite.agent_name || 'this agent'}?`}
      description={
        inviteDialog.invite.kind === 'transfer'
          ? inviteDialog.action === 'accept'
            ? 'Ownership will move to your personal workspace. The original owner will be notified, and the agent may be paused if you are at your agent limit.'
            : 'The original owner will be notified, and ownership will not change.'
          : inviteDialog.action === 'accept'
            ? 'You will get shared access to this agent. Ownership and the owner\'s access will not change.'
            : 'You will not receive access to this agent.'
      }
      confirmLabel={inviteDialog.action === 'accept'
        ? inviteDialog.invite.kind === 'transfer' ? 'Accept transfer' : 'Accept invite'
        : 'Decline invite'}
      busy={inviteBusy}
      danger={inviteDialog.action === 'decline'}
      icon={inviteDialog.action === 'accept' ? Check : X}
      onConfirm={handleConfirmInvite}
      onClose={closeInviteDialog}
      localError={inviteError}
    >
      <AgentInviteDetails invite={inviteDialog.invite} />
    </ActionConfirmDialog>
  ) : null

  const renderListContent = useCallback((variant: 'drawer' | 'sidebar', collapsedView: boolean) => {
    const sourceAgents = collapsedView ? collapsedFilteredAgents : agents
    const showInvites = !collapsedView && agentInvites.length > 0
    const hasListRows = hasAgents || showInvites
    const renderAgentItem = (agent: AgentRosterEntry, isFavorite: boolean) => (
      <AgentListItem
        variant={variant}
        agent={agent}
        isActive={agent.id === activeAgentId}
        isSwitching={agent.id === switchingAgentId}
        isFavorite={isFavorite}
        isMuted={mutedAgentIdSet.has(agent.id)}
        onSelect={handleAgentSelect}
        onOpenContextMenu={openAgentContextMenu}
        onToggleFavorite={onToggleAgentFavorite}
        collapsed={collapsedView}
        showFavoriteToggle={!collapsedView}
      />
    )
    const rows: VirtualRosterRow[] = []
    if (onCreateAgent) {
      rows.push({
        key: 'create-agent',
        content: (
          !collapsedView && teamTemplateMenu ? (
            <AgentCreateSplitButton
              variant={variant}
              onCreateAgent={handleCreateAgent}
              createAgentDisabled={createAgentDisabled}
              createAgentButtonDisabled={createAgentButtonDisabled}
              createAgentDisabledReason={createAgentDisabledReason}
              menu={teamTemplateMenu}
            />
          ) : (
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
          )
        ),
      })
    }
    rows.push({
      key: 'empty-state',
      content: (
        <AgentEmptyState
          variant={variant}
          hasAgents={hasListRows}
          loading={loading}
          errorMessage={errorMessage}
          filteredCount={sourceAgents.length + (showInvites ? agentInvites.length : 0)}
          searchQuery=""
        />
      ),
    })
    const pushAgent = (agent: AgentRosterEntry, isFavorite: boolean) => rows.push({
      key: `agent:${agent.id}`,
      agentId: agent.id,
      content: renderAgentItem(agent, isFavorite),
    })
    const pushHeader = (key: string, label: string, count: number, headerLoading = false) => rows.push({
      key,
      content: <AgentListSectionHeader variant={variant} label={label} count={count} loading={headerLoading} />,
    })
    if (collapsedView) {
      sourceAgents.forEach((agent) => pushAgent(agent, favoriteAgentIdSet.has(agent.id)))
    } else {
      if (showInvites) {
        pushHeader('header:invites', 'Invites', agentInvites.length)
        agentInvites.forEach((invite) => rows.push({
          key: `invite:${invite.kind}:${invite.id}`,
          content: (
            <AgentInviteSidebarItem
              variant={variant}
              invite={invite}
              disabled={!onRespondInvite || inviteBusy}
              onRespond={openInviteDialog}
            />
          ),
        }))
      }
      if (hasFavoritesInRoster) {
        pushHeader('header:favorites', 'Favorites', favoriteFilteredAgents.length)
        favoriteFilteredAgents.forEach((agent) => pushAgent(agent, true))
        pushHeader('header:all', 'All agents', allFilteredAgents.length, loading)
        allFilteredAgents.forEach((agent) => pushAgent(agent, false))
      } else {
        pushHeader('header:all', 'All agents', sourceAgents.length, loading)
        sourceAgents.forEach((agent) => pushAgent(agent, false))
      }
    }
    return (
      <VirtualizedRosterSurface
        rows={rows}
        className={variant === 'drawer' ? 'agent-drawer-list' : 'chat-sidebar-agent-list'}
        estimateSize={collapsedView ? 52 : 64}
        gap={variant === 'drawer' ? 6 : 2}
        overscan={6}
        role="list"
        enabled={variant === 'sidebar' || drawerOpen}
        scrollToAgentId={scrollToAgentId}
        onScrolledToAgent={onScrolledToAgent}
      />
    )
  }, [
    activeAgentId,
    agents,
    allFilteredAgents,
    agentInvites,
    collapsedFilteredAgents,
    createAgentButtonDisabled,
    createAgentDisabled,
    createAgentDisabledReason,
    drawerOpen,
    errorMessage,
    favoriteAgentIdSet,
    favoriteFilteredAgents,
    handleAgentSelect,
    handleCreateAgent,
    hasAgents,
    hasFavoritesInRoster,
    loading,
    mutedAgentIdSet,
    onCreateAgent,
    inviteBusy,
    onRespondInvite,
    onScrolledToAgent,
    onToggleAgentFavorite,
    openAgentContextMenu,
    openInviteDialog,
    scrollToAgentId,
    switchingAgentId,
    teamTemplateMenu,
  ])

  const messageSearchPanel = (
    <MessageSearchPanel
      key={`${contextSwitcher?.current.type ?? settings?.context?.type}:${contextSwitcher?.current.id ?? settings?.context?.id}`}
      agents={agents}
      context={contextSwitcher?.current ?? settings?.context ?? null}
      viewerKey={settings?.viewerEmail ?? null}
      agentsLoading={loading}
      state={messageSearchState}
      onStateChange={updateMessageSearchState}
      onAgentSelect={handleAgentSelect}
      onResultSelect={isMobile ? () => setDrawerOpen(false) : undefined}
    />
  )

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
      <div className="chat-sidebar-mobile-content">
        <AgentChatButton
          className="agent-fab"
          variant="solid"
          onClick={() => setDrawerOpen(true)}
          aria-label="Switch agent"
          aria-expanded={drawerOpen}
        >
          <AgentChatAvatar
            name={activeAgent?.name || 'Agent'}
            avatarUrl={activeAgent?.avatarUrl}
            className="agent-fab-avatar"
            imageClassName="agent-fab-avatar-image"
            textClassName="agent-fab-avatar-text"
          />
          <span className="agent-fab-switch-badge" aria-hidden="true">
            <ArrowLeftRight className="h-2.5 w-2.5" />
          </span>
        </AgentChatButton>

        <AgentChatMobileSheet
          open={drawerOpen}
          keepMounted={true}
          tone="sidebar"
          onClose={() => {
            if (showSettingsView) {
              onBackFromEmbeddedSettings?.()
              return
            }
            if (showCustomGalleryShellPanel) {
              onGalleryShellPageChange?.('agents')
              setDrawerViewMode('list')
            }
            setDrawerOpen(false)
          }}
          title={
            messageSearchOpen
              ? 'Search'
              : showSettingsView
              ? embeddedSettingsTitle
              : (drawerViewMode === 'gallery' && galleryShellPage !== 'agents' ? shellTitle : 'Switch agent')
          }
          // Not PanelLeft: that glyph is the interactive expand-sidebar control on desktop, and
          // as inert sheet decoration it read as a dead button (bug #437).
          icon={ArrowLeftRight}
          bodyPadding={false}
          headerAccessory={!messageSearchOpen && !showSettingsView && mobileContextSwitcher ? (
            <AgentChatContextSwitcher {...mobileContextSwitcher} variant="drawer" />
          ) : null}
          ariaLabel={messageSearchOpen ? 'Search agents and messages' : showSettingsView ? embeddedSettingsTitle : 'Switch agent'}
        >
          {messageSearchOpen ? messageSearchPanel : null}
          {!messageSearchOpen && showSettingsView ? embeddedSettingsPanel : null}
          {!messageSearchOpen && !showSettingsView && !showCustomGalleryShellPanel ? (
            <AgentSearchInput
              variant="drawer"
              value={searchQuery}
              onChange={setSearchQuery}
              onClear={() => setSearchQuery('')}
              onFocus={openMessageSearch}
            />
          ) : null}
          {!messageSearchOpen && !showSettingsView && !showCustomGalleryShellPanel && showSortToggle ? (
            <AgentSortToggle
              variant="drawer"
              value={rosterSortMode}
              onChange={(mode) => onRosterSortModeChange?.(mode)}
            />
          ) : null}
          {!messageSearchOpen && !showSettingsView && !showCustomGalleryShellPanel && hasAgents ? (
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

          {!messageSearchOpen && !showSettingsView && showGalleryShellSwitcher ? (
            <div className="agent-drawer-shell-switcher">
              <SelectionShellPageSwitcher
                currentPage={galleryShellPage}
                onSelectPage={onGalleryShellPageChange!}
                showOrganization={showOrganizationShellPage}
              />
            </div>
          ) : null}
          {!messageSearchOpen && !showSettingsView && drawerViewMode === 'gallery' ? (
            showCustomGalleryShellPanel ? (
              <div className="agent-gallery-scroll" data-variant="drawer">
                {galleryShellPanel}
              </div>
            ) : (
              <ChatSidebarGallery
                variant="drawer"
                agents={agents}
                favoriteAgentIds={favoriteAgentIds}
                activeAgentId={activeAgentId}
                switchingAgentId={switchingAgentId}
                hasAgents={hasAgents}
                loading={loading}
                errorMessage={errorMessage}
                searchQuery=""
                onSelectAgent={handleAgentSelect}
                onConfigureAgent={onConfigureAgent}
                onToggleAgentFavorite={onToggleAgentFavorite}
                onCreateAgent={onCreateAgent ? handleCreateAgent : undefined}
                createAgentButtonDisabled={createAgentButtonDisabled}
                createAgentDisabled={createAgentDisabled}
                createAgentDisabledReason={createAgentDisabledReason}
                teamTemplateMenu={teamTemplateMenu}
                enabled={drawerOpen}
                scrollToAgentId={scrollToAgentId}
                onScrolledToAgent={onScrolledToAgent}
              />
            )
          ) : !messageSearchOpen && !showSettingsView ? (
            renderListContent('drawer', false)
          ) : null}
          {!messageSearchOpen && !showSettingsView && settings ? (
            <SidebarSettingsMenu
              {...settings}
              variant="drawer"
              bottomAccessory={settings.isProprietaryMode ? <ProductAnnouncementBell variant="mobile" /> : null}
            />
          ) : null}
        </AgentChatMobileSheet>
      </div>
      {agentContextMenuElement}
      {inviteDialogElement}
      </>
    )
  }

  return (
    <>
    <aside
      className={`chat-sidebar chat-sidebar--${messageSearchOpen ? 'list' : desktopMode}`}
      data-collapsed={messageSearchOpen ? false : collapsed}
      data-sidebar-mode={messageSearchOpen ? 'list' : desktopMode}
      data-search-open={messageSearchOpen ? 'true' : undefined}
    >
      <div className="chat-sidebar-inner">
        <div
          className="chat-sidebar-header"
          data-collapsed={messageSearchOpen ? 'false' : collapsed ? 'true' : 'false'}
          data-has-center={showHeaderCenter ? 'true' : 'false'}
        >
          <div className="chat-sidebar-header-start">
            {messageSearchOpen || !collapsed ? (
              <a href="/" className="chat-sidebar-logo-link">
                <img src={sidebarLogoSrc} alt={sidebarLogoAlt} className="chat-sidebar-logo" />
              </a>
            ) : null}
          </div>
          {showHeaderCenter ? (
            <div className="chat-sidebar-header-center">
              {contextSwitcher ? (
                <AgentChatContextSwitcher {...contextSwitcher} collapsed={collapsed} />
              ) : null}
              {showHeaderPageSwitcher ? (
                <SelectionShellPageSwitcher
                  currentPage={galleryShellPage}
                  onSelectPage={onGalleryShellPageChange!}
                  showOrganization={showOrganizationShellPage}
                />
              ) : null}
            </div>
          ) : null}
          <div className="chat-sidebar-header-actions">
            {messageSearchOpen || collapsed ? (
              <button
                type="button"
                className="chat-sidebar-toggle"
                onClick={() => {
                  if (messageSearchOpen) {
                    setMessageSearchOpen(false)
                    return
                  }
                  openMessageSearch()
                }}
                aria-label={messageSearchOpen ? 'Close search' : 'Search agents and messages'}
                title={messageSearchOpen ? 'Close search' : 'Search agents and messages'}
              >
                {messageSearchOpen ? <X className="h-4 w-4" /> : <Search className="h-4 w-4" />}
              </button>
            ) : null}
            {!messageSearchOpen && !collapsed ? (
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
            {!messageSearchOpen && !galleryMode && !showSettingsView ? (
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

        {messageSearchOpen ? messageSearchPanel : <div className="chat-sidebar-section">
          {showSettingsView ? (
            <div className="chat-sidebar-section-header">
              <span className="chat-sidebar-section-title">{embeddedSettingsTitle}</span>
            </div>
          ) : showCustomGalleryShellPanel ? null : (
            <div className="chat-sidebar-section-header">
              <span className="chat-sidebar-section-title">Agents</span>
              {!collapsed && hasAgents && !loading ? (
                <span className="chat-sidebar-section-count">{agents.length}</span>
              ) : null}
            </div>
          )}

          {!collapsed && !showSettingsView && !showCustomGalleryShellPanel ? (
            <div
              className="chat-sidebar-controls"
              data-gallery={galleryMode ? 'true' : 'false'}
            >
              <AgentSearchInput
                variant="sidebar"
                value={searchQuery}
                onChange={setSearchQuery}
                onClear={() => setSearchQuery('')}
                onFocus={openMessageSearch}
              />
              {showSortToggle ? (
                <AgentSortToggle
                  variant="sidebar"
                  value={rosterSortMode}
                  onChange={(mode) => onRosterSortModeChange?.(mode)}
                />
              ) : null}
            </div>
          ) : null}

          {showSettingsView ? (
            <div className="min-h-0 flex-1 overflow-y-auto">
              {embeddedSettingsPanel}
            </div>
          ) : showCustomGalleryShellPanel ? (
            <div className="agent-gallery-scroll" data-variant="sidebar">
              {galleryShellPanel}
            </div>
          ) : galleryMode ? (
            <ChatSidebarGallery
              variant="sidebar"
              agents={agents}
              favoriteAgentIds={favoriteAgentIds}
              activeAgentId={activeAgentId}
              switchingAgentId={switchingAgentId}
              hasAgents={hasAgents}
              loading={loading}
              errorMessage={errorMessage}
              searchQuery=""
              onSelectAgent={handleAgentSelect}
              onConfigureAgent={onConfigureAgent}
              onToggleAgentFavorite={onToggleAgentFavorite}
              onCreateAgent={onCreateAgent ? handleCreateAgent : undefined}
              createAgentButtonDisabled={createAgentButtonDisabled}
              createAgentDisabled={createAgentDisabled}
              createAgentDisabledReason={createAgentDisabledReason}
              teamTemplateMenu={teamTemplateMenu}
              scrollToAgentId={scrollToAgentId}
              onScrolledToAgent={onScrolledToAgent}
            />
          ) : (
            renderListContent('sidebar', collapsed)
          )}
        </div>}

        {!messageSearchOpen && !showSettingsView && settings ? (
          <SidebarSettingsMenu
            {...settings}
            variant="sidebar"
            collapsed={collapsed}
            bottomAccessory={settings.isProprietaryMode ? <ProductAnnouncementBell variant="sidebar" /> : null}
          />
        ) : null}
      </div>
    </aside>
    {agentContextMenuElement}
    {inviteDialogElement}
    </>
  )
})
