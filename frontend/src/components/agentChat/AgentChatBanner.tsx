import { memo, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, CreditCard, EllipsisVertical, ListTodo, Mail, MessageSquare, Settings, Share2, Stethoscope, UserPlus, X, Zap } from 'lucide-react'
import { Button, Dialog, DialogTrigger, Popover } from 'react-aria-components'

import { useSubscriptionStore } from '../../stores/subscriptionStore'
import { normalizeHexColor } from '../../util/color'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'
import type { PlanSnapshot } from '../../types/agentChat'
import type { DailyCreditsStatus } from '../../types/dailyCredits'
import type { SignupPreviewState } from '../../types/agentRoster'
import type { AgentChatSidebarMode } from './sidebarMode'
import { AgentChatAvatar, AgentChatButton, AgentChatMenuItem } from './uiPrimitives'

export type ConnectionStatusTone = 'connected' | 'connecting' | 'reconnecting' | 'offline' | 'error'

type AgentChatBannerProps = {
  agentId?: string | null
  agentName: string
  agentAvatarUrl?: string | null
  agentColorHex?: string | null
  agentEmail?: string | null
  agentSms?: string | null
  auditUrl?: string | null
  isOrgOwned?: boolean
  canManageAgent?: boolean
  isCollaborator?: boolean
  connectionStatus?: ConnectionStatusTone
  connectionLabel?: string
  connectionDetail?: string | null
  planSnapshot?: PlanSnapshot | null
  planPanelMode?: 'docked' | 'hidden'
  onPlanOpen?: () => void
  onPlanHoverChange?: (hovered: boolean) => void
  processingActive?: boolean
  dailyCreditsStatus?: DailyCreditsStatus | null
  showPurchaseSeatsButton?: boolean
  onPurchaseSeats?: () => void
  onSettingsOpen?: () => void
  settingsDisabled?: boolean
  settingsDisabledReason?: string | null
  onBlockedSettingsClick?: (location: 'banner_desktop' | 'banner_mobile') => void
  onClose?: () => void
  onShare?: () => void
  shareDisabled?: boolean
  shareDisabledReason?: string | null
  onBlockedShareClick?: (location: 'banner_desktop' | 'banner_mobile') => void
  onPublicShare?: () => void
  publicShareDisabled?: boolean
  publicShareDisabledReason?: string | null
  sidebarMode?: AgentChatSidebarMode
  signupPreviewState?: SignupPreviewState
  children?: ReactNode
}

function ConnectionBadge({ status, label }: { status: ConnectionStatusTone; label: string }) {
  const isConnected = status === 'connected'
  const isReconnecting = status === 'reconnecting' || status === 'connecting'

  return (
    <div className="banner-connection" data-status={status}>
      <span className="banner-connection-dot" data-pulse={isReconnecting ? 'true' : 'false'} />
      <span className="banner-connection-label">{label}</span>
      {isConnected && <Check size={10} className="banner-connection-check" strokeWidth={3} />}
    </div>
  )
}

export const AgentChatBanner = memo(function AgentChatBanner({
  agentId,
  agentName,
  agentAvatarUrl,
  agentColorHex,
  agentEmail,
  agentSms,
  auditUrl,
  isOrgOwned = false,
  canManageAgent = true,
  isCollaborator = false,
  connectionStatus = 'connecting',
  connectionLabel = 'Connecting',
  planSnapshot,
  planPanelMode = 'docked',
  onPlanOpen,
  onPlanHoverChange,
  processingActive = false,
  dailyCreditsStatus,
  showPurchaseSeatsButton = false,
  onPurchaseSeats,
  onSettingsOpen,
  settingsDisabled = false,
  settingsDisabledReason = null,
  onBlockedSettingsClick,
  onClose,
  onShare,
  shareDisabled = false,
  shareDisabledReason = null,
  onBlockedShareClick,
  onPublicShare,
  publicShareDisabled = false,
  publicShareDisabledReason = null,
  sidebarMode = 'list',
  signupPreviewState = 'none',
  children,
}: AgentChatBannerProps) {
  const trimmedName = agentName.trim() || 'Agent'
  const accentColor = normalizeHexColor(agentColorHex) || '#6366f1'
  const bannerRef = useRef<HTMLDivElement | null>(null)
  const [animate, setAnimate] = useState(false)
  const hasAnimatedRef = useRef(false)

  // Subscription state
  const {
    currentPlan,
    isProprietaryMode,
    openUpgradeModal,
    ensureAuthenticated,
  } = useSubscriptionStore()
  const canShowBannerActions = canManageAgent !== false && !isCollaborator
  const showPurchaseSeatsCta = canShowBannerActions && showPurchaseSeatsButton && Boolean(onPurchaseSeats)

  // Determine if we should show upgrade button and what it should say
  // Only show in proprietary mode, and not for org-owned agents (billing is handled at org level)
  const showUpgradeButton = canShowBannerActions
    && !showPurchaseSeatsCta
    && isProprietaryMode
    && !isOrgOwned
    && (currentPlan === 'free' || currentPlan === 'startup')
  const targetPlan = currentPlan === 'free' ? 'startup' : 'scale'
  const upgradeButtonLabel = currentPlan === 'free' ? 'Upgrade to Pro' : 'Upgrade to Scale'

  const handleBannerUpgradeClick = useCallback(async () => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      return
    }
    track(AnalyticsEvent.UPGRADE_BANNER_CLICKED, {
      currentPlan,
      targetPlan,
    })
    openUpgradeModal('banner')
  }, [currentPlan, ensureAuthenticated, openUpgradeModal, targetPlan])

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

  // Animate on first appearance only (not when switching agents)
  useEffect(() => {
    if (planSnapshot && !hasAnimatedRef.current) {
      hasAnimatedRef.current = true
      setAnimate(false)
      const timer = setTimeout(() => setAnimate(true), 30)
      return () => clearTimeout(timer)
    }
    // If we already have plan data, ensure animate stays true
    if (planSnapshot && hasAnimatedRef.current && !animate) {
      setAnimate(true)
    }
  }, [planSnapshot?.doneCount, planSnapshot?.todoCount, planSnapshot?.doingCount, animate])

  const hasPlan = planSnapshot && (planSnapshot.todoCount + planSnapshot.doingCount + planSnapshot.doneCount) > 0
  const currentTask = hasPlan && planSnapshot.doingTitles.length > 0 ? planSnapshot.doingTitles[0] : null
  const hardLimitReached = Boolean(dailyCreditsStatus?.hardLimitReached || dailyCreditsStatus?.hardLimitBlocked)
  const softTargetExceeded = Boolean(dailyCreditsStatus?.softTargetExceeded)
  const showSettingsButton = canShowBannerActions && Boolean(onSettingsOpen)
  const showShareButton = canShowBannerActions && Boolean(onShare)
  const showPublicShareButton = canShowBannerActions && Boolean(onPublicShare)
  const showAuditButton = Boolean(auditUrl)
  const showAttentionDot = softTargetExceeded || hardLimitReached
  const settingsLabel = hardLimitReached
    ? 'Daily task limit reached. Open agent settings'
    : 'Open agent settings'
  const [overflowMenuOpen, setOverflowMenuOpen] = useState(false)
  const showMobileOverflow = showShareButton || showPublicShareButton || showAuditButton || showSettingsButton
  const shareLabel = shareDisabledReason || 'Invite collaborators'
  const publicShareLabel = publicShareDisabledReason || 'Share this agent'
  const resolvedSettingsLabel = settingsDisabledReason || settingsLabel
  const planButtonLabel = planPanelMode === 'hidden' ? 'Show plan' : 'Hide plan'
  const trackableShareDisabled = shareDisabled && Boolean(onBlockedShareClick)
  const trackableSettingsDisabled = settingsDisabled && Boolean(onBlockedSettingsClick)
  const previewAnalyticsEnabled = signupPreviewState !== 'none'

  const handleShareClick = useCallback((location: 'banner_desktop' | 'banner_mobile') => {
    if (shareDisabled && onBlockedShareClick) {
      onBlockedShareClick(location)
      return
    }
    onShare?.()
  }, [onBlockedShareClick, onShare, shareDisabled])

  const handlePublicShareClick = useCallback(() => {
    if (publicShareDisabled) {
      return
    }
    onPublicShare?.()
  }, [onPublicShare, publicShareDisabled])

  const handleSettingsClick = useCallback((location: 'banner_desktop' | 'banner_mobile') => {
    if (settingsDisabled && onBlockedSettingsClick) {
      onBlockedSettingsClick(location)
      return
    }
    onSettingsOpen?.()
  }, [onBlockedSettingsClick, onSettingsOpen, settingsDisabled])

  const handlePreviewContactClick = useCallback((channel: 'email' | 'sms') => {
    if (!previewAnalyticsEnabled) {
      return
    }
    track(AnalyticsEvent.SIGNUP_PREVIEW_CONTACT_CLICKED, {
      agentId: agentId ?? undefined,
      signupPreviewState,
      channel,
      source: 'banner_contact_link',
    })
  }, [agentId, previewAnalyticsEnabled, signupPreviewState])

  const handleCloseClick = useCallback(() => {
    if (previewAnalyticsEnabled) {
      track(AnalyticsEvent.SIGNUP_PREVIEW_CLOSED, {
        agentId: agentId ?? undefined,
        signupPreviewState,
        source: 'banner_close',
      })
    }
    onClose?.()
  }, [agentId, onClose, previewAnalyticsEnabled, signupPreviewState])

  return (
    <div className="banner-shell" data-sidebar-mode={sidebarMode} ref={bannerRef}>
      <div
        className="banner"
        style={{ '--banner-accent': accentColor } as React.CSSProperties}
      >
        {/* Left: Avatar + Info */}
        <div className="banner-left">
          <AgentChatAvatar
            name={trimmedName}
            avatarUrl={agentAvatarUrl}
            className="banner-avatar"
            imageClassName="banner-avatar-image"
            textClassName="banner-avatar-text"
          />
          <div className="banner-info">
            <div className="banner-top-row">
              <span className="banner-name">{trimmedName}</span>
              {(agentEmail || agentSms) ? (
                <span className="banner-contact-icons">
                  {agentEmail ? (
                    <a
                      href={`mailto:${agentEmail}`}
                      className="banner-contact-link"
                      title={agentEmail}
                      onClick={() => handlePreviewContactClick('email')}
                    >
                      <Mail size={12} strokeWidth={2} />
                      <span className="banner-contact-text">{agentEmail}</span>
                    </a>
                  ) : null}
                  {agentSms ? (
                    <a
                      href={`sms:${agentSms}`}
                      className="banner-contact-link"
                      title={agentSms}
                      onClick={() => handlePreviewContactClick('sms')}
                    >
                      <MessageSquare size={12} strokeWidth={2} />
                      <span className="banner-contact-text">{agentSms}</span>
                    </a>
                  ) : null}
                </span>
              ) : null}
              <ConnectionBadge status={connectionStatus} label={connectionLabel} />
            </div>
            {hasPlan && currentTask ? (
              <div className={`banner-task ${animate ? 'banner-task--animate' : ''}`}>
                <span className={`banner-task-dot ${processingActive ? 'banner-task-dot--active' : ''}`} />
                <span className="banner-task-title">{currentTask}</span>
              </div>
            ) : null}
          </div>
        </div>

        {/* Right: Upgrade button + Close button */}
        <div className="banner-right">
          {showPurchaseSeatsCta ? (
            <AgentChatButton
              className="banner-upgrade"
              tone="warning"
              variant="solid"
              size="sm"
              onClick={onPurchaseSeats}
              aria-label="Purchase Seats"
              title="Purchase Seats"
            >
              <CreditCard size={14} strokeWidth={2} />
              <span>Purchase Seats</span>
            </AgentChatButton>
          ) : null}
          {showUpgradeButton && (
            <AgentChatButton
              className="banner-upgrade"
              tone="warning"
              variant="solid"
              size="sm"
              onClick={handleBannerUpgradeClick}
              aria-label={upgradeButtonLabel}
              title={upgradeButtonLabel}
            >
              <Zap size={14} strokeWidth={2} />
              <span>{upgradeButtonLabel}</span>
            </AgentChatButton>
          )}
          {onPlanOpen ? (
            <button
              type="button"
              className="banner-plan"
              onClick={onPlanOpen}
              onMouseEnter={() => onPlanHoverChange?.(true)}
              onMouseLeave={() => onPlanHoverChange?.(false)}
              onFocus={(event) => {
                if (event.currentTarget.matches(':focus-visible')) {
                  onPlanHoverChange?.(true)
                }
              }}
              onBlur={() => onPlanHoverChange?.(false)}
              aria-label={planButtonLabel}
              title={planButtonLabel}
              data-plan-mode={planPanelMode}
            >
              <ListTodo size={14} strokeWidth={2} />
              <span className="banner-plan-label">Plan</span>
              {hasPlan ? <span className="banner-plan-count">{planSnapshot.doingCount + planSnapshot.todoCount}</span> : null}
            </button>
          ) : null}
          {showShareButton ? (
            <AgentChatButton
              className="banner-share banner-desktop-only"
              variant="soft"
              size="sm"
              onClick={() => handleShareClick('banner_desktop')}
              aria-label={shareLabel}
              title={shareLabel}
              disabled={shareDisabled && !trackableShareDisabled}
              aria-disabled={shareDisabled ? 'true' : undefined}
            >
              <UserPlus size={14} strokeWidth={2} />
              <span className="banner-share-label">Collaborate</span>
            </AgentChatButton>
          ) : null}
          {showPublicShareButton ? (
            <AgentChatButton
              className="banner-share banner-desktop-only"
              variant="soft"
              size="sm"
              onClick={handlePublicShareClick}
              aria-label={publicShareLabel}
              title={publicShareLabel}
              disabled={publicShareDisabled}
              aria-disabled={publicShareDisabled ? 'true' : undefined}
            >
              <Share2 size={14} strokeWidth={2} />
              <span className="banner-share-label">Share</span>
            </AgentChatButton>
          ) : null}
          {showAuditButton ? (
            <a
              className="banner-settings banner-desktop-only"
              href={auditUrl ?? undefined}
              target="_blank"
              rel="noreferrer"
              aria-label="Open audit timeline"
              title="Open audit timeline"
            >
              <Stethoscope size={16} />
            </a>
          ) : null}
          {showMobileOverflow ? (
            <DialogTrigger isOpen={overflowMenuOpen} onOpenChange={setOverflowMenuOpen}>
              <Button
                className="agent-chat-button banner-settings banner-mobile-only"
                aria-label="More actions"
              >
                <EllipsisVertical size={16} />
              </Button>
              <Popover className="banner-overflow-popover">
                <Dialog className="banner-overflow-menu">
                  {showShareButton || showPublicShareButton || showAuditButton || showSettingsButton ? (
                    <div className="banner-overflow-section">
                      <div className="banner-overflow-heading">Actions</div>
                      <div className="banner-overflow-items">
                        {showShareButton ? (
                          <AgentChatMenuItem
                            type="button"
                            className="banner-overflow-item"
                            onClick={() => {
                              handleShareClick('banner_mobile')
                              if (!shareDisabled) {
                                setOverflowMenuOpen(false)
                              }
                            }}
                            disabled={shareDisabled && !trackableShareDisabled}
                            aria-disabled={shareDisabled ? 'true' : undefined}
                            title={shareLabel}
                          >
                            <span className="banner-overflow-item-icon" aria-hidden="true">
                              <UserPlus size={14} />
                            </span>
                            <span className="banner-overflow-item-copy">
                              <span className="banner-overflow-item-label">Collaborate</span>
                            </span>
                          </AgentChatMenuItem>
                        ) : null}
                        {showPublicShareButton ? (
                          <AgentChatMenuItem
                            type="button"
                            className="banner-overflow-item"
                            onClick={() => {
                              handlePublicShareClick()
                              if (!publicShareDisabled) {
                                setOverflowMenuOpen(false)
                              }
                            }}
                            disabled={publicShareDisabled}
                            aria-disabled={publicShareDisabled ? 'true' : undefined}
                            title={publicShareLabel}
                          >
                            <span className="banner-overflow-item-icon" aria-hidden="true">
                              <Share2 size={14} />
                            </span>
                            <span className="banner-overflow-item-copy">
                              <span className="banner-overflow-item-label">Share</span>
                            </span>
                          </AgentChatMenuItem>
                        ) : null}
                        {showAuditButton ? (
                          <AgentChatMenuItem
                            as="a"
                            className="banner-overflow-item"
                            href={auditUrl ?? undefined}
                            target="_blank"
                            rel="noreferrer"
                            onClick={() => setOverflowMenuOpen(false)}
                          >
                            <span className="banner-overflow-item-icon" aria-hidden="true">
                              <Stethoscope size={14} />
                            </span>
                            <span className="banner-overflow-item-copy">
                              <span className="banner-overflow-item-label">Audit timeline</span>
                            </span>
                          </AgentChatMenuItem>
                        ) : null}
                        {showSettingsButton ? (
                          <AgentChatMenuItem
                            type="button"
                            className="banner-overflow-item"
                            onClick={() => {
                              handleSettingsClick('banner_mobile')
                              if (!settingsDisabled) {
                                setOverflowMenuOpen(false)
                              }
                            }}
                            disabled={settingsDisabled && !trackableSettingsDisabled}
                            aria-disabled={settingsDisabled ? 'true' : undefined}
                            title={resolvedSettingsLabel}
                          >
                            <span className="banner-overflow-item-icon" aria-hidden="true">
                              <Settings size={14} />
                            </span>
                            <span className="banner-overflow-item-copy">
                              <span className="banner-overflow-item-label">Settings</span>
                            </span>
                          </AgentChatMenuItem>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </Dialog>
              </Popover>
            </DialogTrigger>
          ) : null}
          {showSettingsButton ? (
            <AgentChatButton
              className="banner-settings banner-desktop-only"
              variant="ghost"
              size="sm"
              data-alert={hardLimitReached ? 'true' : 'false'}
              onClick={() => handleSettingsClick('banner_desktop')}
              aria-label={resolvedSettingsLabel}
              title={resolvedSettingsLabel}
              disabled={settingsDisabled && !trackableSettingsDisabled}
              aria-disabled={settingsDisabled ? 'true' : undefined}
            >
              <Settings size={16} />
              {showAttentionDot ? (
                <span className="banner-settings-dot" data-alert={hardLimitReached ? 'true' : 'false'} />
              ) : null}
            </AgentChatButton>
          ) : null}
          {onClose ? (
            <AgentChatButton
              className="banner-close"
              variant="ghost"
              size="sm"
              onClick={handleCloseClick}
              aria-label="Close"
            >
              <X size={16} strokeWidth={1.75} />
            </AgentChatButton>
          ) : null}
        </div>

      </div>
      {children ? <div className="banner-secondary">{children}</div> : null}
    </div>
  )
})
