import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { Bell, Building2, ChevronDown, CircleHelp, ClipboardList, CreditCard, KeyRound, LockKeyhole, ServerCog, Settings, User, UserRound } from 'lucide-react'
import { Button, Dialog, Popover } from 'react-aria-components'

import type { ConsoleContext } from '../../api/context'

export type SidebarTaskCreditsInfo = {
  usedToday: number | null
  remaining: number | null
  resetOn: string | null
  unlimited?: boolean
}

export type SidebarSettingsInfo = {
  context?: ConsoleContext | null
  viewerEmail?: string | null
  isProprietaryMode: boolean
  billingUrl?: string | null
  onOpenBilling?: (() => void) | null
  usageUrl?: string | null
  onOpenUsage?: (() => void) | null
  apiKeysUrl?: string | null
  onOpenApiKeys?: (() => void) | null
  profileUrl?: string | null
  onOpenProfile?: (() => void) | null
  organizationUrl?: string | null
  onOpenOrganization?: (() => void) | null
  secretsUrl?: string | null
  onOpenSecrets?: (() => void) | null
  globalSecretsUrl?: string | null
  integrationsUrl?: string | null
  onOpenIntegrations?: (() => void) | null
  advancedMcpUrl?: string
  notificationsEnabled?: boolean
  notificationStatus?: 'off' | 'on' | 'needs_permission' | 'blocked'
  onNotificationsEnabledChange?: (enabled: boolean) => void
  taskCredits?: SidebarTaskCreditsInfo | null
  onOpenHelp?: (() => void) | null
}

type SidebarSettingsMenuProps = SidebarSettingsInfo & {
  variant?: 'sidebar' | 'drawer'
  collapsed?: boolean
  bottomAccessory?: ReactNode
}

const creditFormatter = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 2,
})

const dateFormatter = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

function formatCreditValue(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '-'
  }
  return creditFormatter.format(value)
}

function formatDateValue(value: string | null | undefined): string {
  if (!value) {
    return '-'
  }
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return dateFormatter.format(date)
}

function resolveNotificationStatusLabel(
  enabled: boolean,
  status: SidebarSettingsInfo['notificationStatus'],
): string {
  if (!enabled) {
    return 'Off'
  }
  if (status === 'needs_permission') {
    return 'Needs browser permission'
  }
  if (status === 'blocked') {
    return 'Blocked in browser'
  }
  return 'On'
}

export function SidebarSettingsMenu({
  context = null,
  viewerEmail = null,
  isProprietaryMode,
  billingUrl = null,
  onOpenBilling = null,
  usageUrl = '/app/usage',
  onOpenUsage = null,
  apiKeysUrl = '/app/api-keys',
  onOpenApiKeys = null,
  profileUrl = '/app/profile',
  onOpenProfile = null,
  organizationUrl = null,
  onOpenOrganization = null,
  secretsUrl = null,
  onOpenSecrets = null,
  globalSecretsUrl = '/app/secrets',
  integrationsUrl = null,
  onOpenIntegrations = null,
  advancedMcpUrl = '/app/integrations',
  notificationsEnabled = true,
  notificationStatus = 'off',
  onNotificationsEnabledChange,
  taskCredits = null,
  onOpenHelp = null,
  variant = 'sidebar',
  collapsed = false,
  bottomAccessory = null,
}: SidebarSettingsMenuProps) {
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const rowRef = useRef<HTMLDivElement | null>(null)
  const actionsRef = useRef<HTMLDivElement | null>(null)
  const popoverRef = useRef<HTMLElement | null>(null)
  const [open, setOpen] = useState(false)
  const [creditsOpen, setCreditsOpen] = useState(false)
  const handleOpenChange = useCallback((nextOpen: boolean) => {
    setOpen(nextOpen)
    if (!nextOpen) {
      setCreditsOpen(false)
    }
  }, [])
  useEffect(() => {
    if (!open || typeof document === 'undefined') {
      return
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Node)) {
        return
      }
      if (actionsRef.current?.contains(target) || popoverRef.current?.contains(target)) {
        return
      }
      handleOpenChange(false)
    }

    document.addEventListener('pointerdown', handlePointerDown, true)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true)
    }
  }, [handleOpenChange, open])
  const identityLabel = useMemo(() => {
    if (context?.type === 'organization') {
      return context.name || 'Team'
    }
    return viewerEmail?.trim() || context?.name || 'Personal workspace'
  }, [context, viewerEmail])
  const canShowBilling = Boolean(isProprietaryMode && (billingUrl || onOpenBilling))
  const canShowUsage = Boolean(usageUrl || onOpenUsage)
  const canShowApiKeys = Boolean(apiKeysUrl || onOpenApiKeys)
  const canShowProfile = Boolean(profileUrl || onOpenProfile)
  const canShowOrganization = Boolean(context?.type === 'organization' && (organizationUrl || onOpenOrganization))
  const resolvedSecretsUrl = secretsUrl ?? globalSecretsUrl
  const canShowSecrets = Boolean(resolvedSecretsUrl || onOpenSecrets)
  const resolvedIntegrationsUrl = integrationsUrl ?? advancedMcpUrl
  const canShowIntegrations = Boolean(resolvedIntegrationsUrl || onOpenIntegrations)
  const canShowTaskCredits = Boolean(isProprietaryMode && taskCredits)
  const remainingLabel = taskCredits?.unlimited
    ? 'Unlimited'
    : formatCreditValue(taskCredits?.remaining)
  const notificationStatusLabel = resolveNotificationStatusLabel(
    notificationsEnabled,
    notificationStatus,
  )
  const handleNotificationToggle = useCallback(() => {
    onNotificationsEnabledChange?.(!notificationsEnabled)
  }, [notificationsEnabled, onNotificationsEnabledChange])
  const handleTriggerPointerDownCapture = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!open) {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    handleOpenChange(false)
  }, [handleOpenChange, open])

  return (
    <div
      className="sidebar-settings"
      data-variant={variant}
      data-collapsed={collapsed ? 'true' : 'false'}
      data-has-help={onOpenHelp ? 'true' : 'false'}
    >
      <div className="sidebar-settings__row" ref={rowRef}>
        <div className="sidebar-settings__actions" ref={actionsRef}>
          <Button
            ref={triggerRef}
            className="sidebar-settings__trigger"
            aria-label="Open settings"
            aria-expanded={open}
            onPointerDownCapture={handleTriggerPointerDownCapture}
            onPress={() => handleOpenChange(!open)}
            data-open={open ? 'true' : 'false'}
          >
            <Settings className="sidebar-settings__trigger-icon" aria-hidden="true" />
            {!collapsed ? <span className="sidebar-settings__trigger-label">Settings</span> : null}
          </Button>
          {onOpenHelp ? (
            <button
              type="button"
              className="sidebar-settings__trigger sidebar-settings__trigger--help"
              aria-label="Contact support"
              title="Contact support"
              onClick={onOpenHelp}
            >
              <CircleHelp className="sidebar-settings__trigger-icon" aria-hidden="true" />
            </button>
          ) : null}
        </div>
        {bottomAccessory ? <div className="sidebar-settings__accessory">{bottomAccessory}</div> : null}
      </div>
      <Popover
        ref={popoverRef}
        triggerRef={triggerRef}
        isOpen={open}
        onOpenChange={handleOpenChange}
        shouldCloseOnInteractOutside={() => true}
        placement="top start"
        containerPadding={0}
        isNonModal
        className="sidebar-settings__popover"
        data-variant={variant}
        data-collapsed={collapsed ? 'true' : 'false'}
      >
        <Dialog className="sidebar-settings__menu" aria-label="Settings menu">
          <div className="sidebar-settings__identity">
            <UserRound className="sidebar-settings__identity-icon" aria-hidden="true" />
            <span className="sidebar-settings__identity-label">{identityLabel}</span>
          </div>
          <div className="sidebar-settings__rule" role="separator" aria-hidden="true" />

          <div className="sidebar-settings__links">
            <button
              type="button"
              className="sidebar-settings__notification-toggle"
              role="switch"
              aria-checked={notificationsEnabled}
              onClick={handleNotificationToggle}
            >
              <span className="sidebar-settings__notification-copy">
                <span className="sidebar-settings__notification-title">
                  <Bell className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Notifications &amp; sound</span>
                </span>
                <span className="sidebar-settings__notification-status">{notificationStatusLabel}</span>
              </span>
              <span
                className="sidebar-settings__switch"
                data-checked={notificationsEnabled ? 'true' : 'false'}
                aria-hidden="true"
              >
                <span className="sidebar-settings__switch-thumb" />
              </span>
            </button>
            <div className="sidebar-settings__rule" role="separator" aria-hidden="true" />
            {canShowProfile ? (
              onOpenProfile ? (
                <button
                  type="button"
                  className="sidebar-settings__link"
                  onClick={() => {
                    handleOpenChange(false)
                    onOpenProfile()
                  }}
                >
                  <User className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Profile</span>
                </button>
              ) : (
                <a className="sidebar-settings__link" href={profileUrl ?? undefined} target="_blank" rel="noreferrer">
                  <User className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Profile</span>
                </a>
              )
            ) : null}
            {canShowOrganization ? (
              onOpenOrganization ? (
                <button
                  type="button"
                  className="sidebar-settings__link"
                  onClick={() => {
                    handleOpenChange(false)
                    onOpenOrganization()
                  }}
                >
                  <Building2 className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Team</span>
                </button>
              ) : (
                <a className="sidebar-settings__link" href={organizationUrl ?? undefined} target="_blank" rel="noreferrer">
                  <Building2 className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Team</span>
                </a>
              )
            ) : null}
            {canShowBilling ? (
              onOpenBilling ? (
                <button
                  type="button"
                  className="sidebar-settings__link"
                  onClick={() => {
                    handleOpenChange(false)
                    onOpenBilling()
                  }}
                >
                  <CreditCard className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Billing</span>
                </button>
              ) : (
                <a className="sidebar-settings__link" href={billingUrl ?? undefined} target="_blank" rel="noreferrer">
                  <CreditCard className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Billing</span>
                </a>
              )
            ) : null}
            {canShowSecrets ? (
              onOpenSecrets ? (
                <button
                  type="button"
                  className="sidebar-settings__link"
                  onClick={() => {
                    handleOpenChange(false)
                    onOpenSecrets()
                  }}
                >
                  <LockKeyhole className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Global Secrets</span>
                </button>
              ) : (
                <a className="sidebar-settings__link" href={resolvedSecretsUrl ?? undefined} target="_blank" rel="noreferrer">
                  <LockKeyhole className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Global Secrets</span>
                </a>
              )
            ) : null}
            {canShowIntegrations ? (
              onOpenIntegrations ? (
                <button
                  type="button"
                  className="sidebar-settings__link"
                  onClick={() => {
                    handleOpenChange(false)
                    onOpenIntegrations()
                  }}
                >
                  <ServerCog className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Integrations &amp; MCP</span>
                </button>
              ) : (
                <a className="sidebar-settings__link" href={resolvedIntegrationsUrl ?? undefined} target="_blank" rel="noreferrer">
                  <ServerCog className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Integrations &amp; MCP</span>
                </a>
              )
            ) : null}
            {canShowApiKeys ? (
              onOpenApiKeys ? (
                <button
                  type="button"
                  className="sidebar-settings__link"
                  onClick={() => {
                    handleOpenChange(false)
                    onOpenApiKeys()
                  }}
                >
                  <KeyRound className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>API Keys</span>
                </button>
              ) : (
                <a className="sidebar-settings__link" href={apiKeysUrl ?? undefined} target="_blank" rel="noreferrer">
                  <KeyRound className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>API Keys</span>
                </a>
              )
            ) : null}
          </div>

          {canShowTaskCredits ? (
            <div className="sidebar-settings__credits">
              <div className="sidebar-settings__rule" role="separator" aria-hidden="true" />
              <button
                type="button"
                className="sidebar-settings__credits-trigger"
                onClick={() => setCreditsOpen((current) => !current)}
                aria-expanded={creditsOpen}
              >
                <span className="sidebar-settings__credits-title">
                  <ClipboardList className="sidebar-settings__link-icon" aria-hidden="true" />
                  <span>Usage</span>
                </span>
                <ChevronDown
                  className="sidebar-settings__credits-chevron"
                  data-open={creditsOpen ? 'true' : 'false'}
                  aria-hidden="true"
                />
              </button>
              {creditsOpen ? (
                <div className="sidebar-settings__credits-details">
                  <dl className="sidebar-settings__credits-list">
                    <div className="sidebar-settings__credits-row">
                      <dt>Used Today</dt>
                      <dd>{formatCreditValue(taskCredits?.usedToday)}</dd>
                    </div>
                    <div className="sidebar-settings__credits-row">
                      <dt>Remaining This Month</dt>
                      <dd>{remainingLabel}</dd>
                    </div>
                    <div className="sidebar-settings__credits-row">
                      <dt>Resets On</dt>
                      <dd>{formatDateValue(taskCredits?.resetOn)}</dd>
                    </div>
                  </dl>
                  {canShowUsage ? (
                    onOpenUsage ? (
                      <button
                        type="button"
                        className="sidebar-settings__credits-view"
                        onClick={() => {
                          handleOpenChange(false)
                          onOpenUsage()
                        }}
                      >
                        View
                      </button>
                    ) : (
                      <a
                        className="sidebar-settings__credits-view"
                        href={usageUrl ?? undefined}
                        target="_blank"
                        rel="noreferrer"
                        onClick={() => handleOpenChange(false)}
                      >
                        View
                      </a>
                    )
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
        </Dialog>
      </Popover>
    </div>
  )
}
