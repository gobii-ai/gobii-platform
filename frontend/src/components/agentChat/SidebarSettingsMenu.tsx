import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Bell,
  ChevronDown,
  ClipboardList,
  CreditCard,
  LockKeyhole,
  ServerCog,
  Settings,
  UserRound,
} from 'lucide-react'
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
  globalSecretsUrl?: string
  advancedMcpUrl?: string
  notificationsEnabled?: boolean
  notificationStatus?: 'off' | 'on' | 'needs_permission' | 'blocked'
  onNotificationsEnabledChange?: (enabled: boolean) => void
  taskCredits?: SidebarTaskCreditsInfo | null
}

type SidebarSettingsMenuProps = SidebarSettingsInfo & {
  variant?: 'sidebar' | 'drawer'
  collapsed?: boolean
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
  globalSecretsUrl = '/console/secrets/',
  advancedMcpUrl = '/console/advanced/mcp-servers/',
  notificationsEnabled = true,
  notificationStatus = 'off',
  onNotificationsEnabledChange,
  taskCredits = null,
  variant = 'sidebar',
  collapsed = false,
}: SidebarSettingsMenuProps) {
  const triggerRef = useRef<HTMLButtonElement | null>(null)
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
      if (triggerRef.current?.contains(target) || popoverRef.current?.contains(target)) {
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
      return context.name || 'Organization'
    }
    return viewerEmail?.trim() || context?.name || 'Personal workspace'
  }, [context, viewerEmail])
  const canShowBilling = Boolean(isProprietaryMode && billingUrl)
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

  return (
    <div
      className={`sidebar-settings sidebar-settings--${variant}`}
      data-collapsed={collapsed ? 'true' : 'false'}
    >
      <Button
        ref={triggerRef}
        className="sidebar-settings__trigger"
        aria-label="Open settings"
        aria-expanded={open}
        onPress={() => handleOpenChange(!open)}
        data-open={open ? 'true' : 'false'}
      >
        <Settings className="sidebar-settings__trigger-icon" aria-hidden="true" />
        {!collapsed ? <span className="sidebar-settings__trigger-label">Settings</span> : null}
      </Button>
      <Popover
        ref={popoverRef}
        triggerRef={triggerRef}
        isOpen={open}
        onOpenChange={handleOpenChange}
        shouldCloseOnInteractOutside={() => true}
        placement="top"
        containerPadding={0}
        isNonModal
        className={`sidebar-settings__popover sidebar-settings__popover--${variant}`}
        data-collapsed={collapsed ? 'true' : 'false'}
      >
        <Dialog className="sidebar-settings__menu" aria-label="Settings menu">
          <div className="sidebar-settings__identity">
            <UserRound className="sidebar-settings__identity-icon" aria-hidden="true" />
            <span className="sidebar-settings__identity-label">{identityLabel}</span>
          </div>
          <div className="sidebar-settings__rule" role="separator" aria-hidden="true" />

          <div className="sidebar-settings__links">
            {canShowBilling ? (
              <a className="sidebar-settings__link" href={billingUrl ?? undefined} target="_blank" rel="noreferrer">
                <CreditCard className="sidebar-settings__link-icon" aria-hidden="true" />
                <span>Billing</span>
              </a>
            ) : null}
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
            <a className="sidebar-settings__link" href={globalSecretsUrl} target="_blank" rel="noreferrer">
              <LockKeyhole className="sidebar-settings__link-icon" aria-hidden="true" />
              <span>Global Secrets</span>
            </a>
            <a className="sidebar-settings__link" href={advancedMcpUrl} target="_blank" rel="noreferrer">
              <ServerCog className="sidebar-settings__link-icon" aria-hidden="true" />
              <span>Integrations &amp; MCP</span>
            </a>
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
                  <span>Task Credits Remaining</span>
                </span>
                <ChevronDown
                  className="sidebar-settings__credits-chevron"
                  data-open={creditsOpen ? 'true' : 'false'}
                  aria-hidden="true"
                />
              </button>
              {creditsOpen ? (
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
              ) : null}
            </div>
          ) : null}
        </Dialog>
      </Popover>
    </div>
  )
}
