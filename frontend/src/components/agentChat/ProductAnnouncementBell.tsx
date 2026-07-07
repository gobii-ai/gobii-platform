import { useCallback, useMemo, useState } from 'react'
import { Bell, Check, Loader2 } from 'lucide-react'
import { Button, Dialog, DialogTrigger, Popover } from 'react-aria-components'

import type { ProductAnnouncement } from '../../api/productAnnouncements'
import { useMarkProductAnnouncementsRead, useProductAnnouncements } from '../../hooks/useProductAnnouncements'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'

type ProductAnnouncementBellProps = {
  variant?: 'sidebar' | 'mobile'
}

function formatPublishedAt(value: string | null): string {
  if (!value) {
    return ''
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return ''
  }
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
  }).format(date)
}

function resolveUnreadLabel(unreadCount: number): string {
  if (unreadCount <= 0) {
    return 'Updates'
  }
  if (unreadCount === 1) {
    return 'Updates, 1 unread'
  }
  return `Updates, ${unreadCount} unread`
}

type AnnouncementPanelProps = {
  announcements: ProductAnnouncement[]
  loading: boolean
  error: boolean
  unreadCount: number
  markBusy: boolean
  variant: 'sidebar' | 'mobile'
  onRetry: () => void
  onMarkAllRead: () => void
  onMarkRead: (announcementId: string) => void
  onAction: (announcement: ProductAnnouncement) => void
}

function AnnouncementPanel({
  announcements,
  loading,
  error,
  unreadCount,
  markBusy,
  variant,
  onRetry,
  onMarkAllRead,
  onMarkRead,
  onAction,
}: AnnouncementPanelProps) {
  return (
    <div className="product-announcement-panel" data-variant={variant}>
      <div className="product-announcement-panel__header">
        <div>
          <h2 className="product-announcement-panel__title">Updates</h2>
          <p className="product-announcement-panel__subtitle">
            {unreadCount > 0 ? `${unreadCount} unread` : 'All caught up'}
          </p>
        </div>
        {unreadCount > 0 ? (
          <button
            type="button"
            className="product-announcement-panel__mark-all"
            onClick={onMarkAllRead}
            disabled={markBusy}
          >
            {markBusy ? <Loader2 className="product-announcement-panel__spinner animate-spin" aria-hidden="true" /> : null}
            <span>Mark all read</span>
          </button>
        ) : null}
      </div>

      {loading ? (
        <div className="product-announcement-panel__state" role="status">
          <Loader2 className="product-announcement-panel__state-icon animate-spin" aria-hidden="true" />
          <span>Loading updates</span>
        </div>
      ) : error ? (
        <div className="product-announcement-panel__state" role="status">
          <span>Updates could not load.</span>
          <button type="button" className="product-announcement-panel__retry" onClick={onRetry}>
            Try again
          </button>
        </div>
      ) : announcements.length === 0 ? (
        <div className="product-announcement-panel__state" role="status">
          No updates
        </div>
      ) : (
        <div className="product-announcement-panel__list">
          {announcements.map((announcement) => (
            <article
              key={announcement.id}
              className="product-announcement-panel__item"
              data-read={announcement.isRead ? 'true' : 'false'}
            >
              <div className="product-announcement-panel__item-header">
                <div className="product-announcement-panel__item-copy">
                  <h3 className="product-announcement-panel__item-title">{announcement.title}</h3>
                  {announcement.publishedAt ? (
                    <time className="product-announcement-panel__item-date" dateTime={announcement.publishedAt}>
                      {formatPublishedAt(announcement.publishedAt)}
                    </time>
                  ) : null}
                </div>
                {!announcement.isRead ? (
                  <button
                    type="button"
                    className="product-announcement-panel__read-button"
                    aria-label={`Mark "${announcement.title}" as read`}
                    onClick={() => onMarkRead(announcement.id)}
                    disabled={markBusy}
                  >
                    <Check className="product-announcement-panel__read-icon" aria-hidden="true" />
                  </button>
                ) : null}
              </div>
              <p className="product-announcement-panel__body">{announcement.body}</p>
              {announcement.actionLabel && announcement.actionUrl ? (
                <button
                  type="button"
                  className="product-announcement-panel__action"
                  onClick={() => onAction(announcement)}
                >
                  {announcement.actionLabel}
                </button>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </div>
  )
}

export function ProductAnnouncementBell({ variant = 'sidebar' }: ProductAnnouncementBellProps) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const announcementsQuery = useProductAnnouncements()
  const markReadMutation = useMarkProductAnnouncementsRead()
  const announcements = announcementsQuery.data?.announcements ?? []
  const unreadCount = announcementsQuery.data?.unreadCount ?? 0
  const hasUnread = announcementsQuery.data?.hasUnread ?? false
  const label = useMemo(() => resolveUnreadLabel(unreadCount), [unreadCount])
  const markBusy = markReadMutation.isPending

  const handleMarkAllRead = useCallback(() => {
    if (unreadCount <= 0) {
      return
    }
    markReadMutation.mutate({ all: true })
  }, [markReadMutation, unreadCount])

  const handleMarkRead = useCallback((announcementId: string) => {
    markReadMutation.mutate({ announcementIds: [announcementId] })
  }, [markReadMutation])

  const handleAction = useCallback(async (announcement: ProductAnnouncement) => {
    const actionUrl = announcement.actionUrl
    if (!actionUrl || typeof window === 'undefined') {
      return
    }
    if (!announcement.isRead) {
      await markReadMutation.mutateAsync({ announcementIds: [announcement.id] })
    }
    window.location.assign(actionUrl)
  }, [markReadMutation])

  const panel = (
    <AnnouncementPanel
      announcements={announcements}
      loading={announcementsQuery.isLoading}
      error={announcementsQuery.isError}
      unreadCount={unreadCount}
      markBusy={markBusy}
      variant={variant}
      onRetry={() => void announcementsQuery.refetch()}
      onMarkAllRead={handleMarkAllRead}
      onMarkRead={handleMarkRead}
      onAction={handleAction}
    />
  )

  if (variant === 'mobile') {
    return (
      <>
        <button
          type="button"
          className="product-announcement-mobile-trigger"
          aria-label={label}
          onClick={() => setMobileOpen(true)}
        >
          <Bell className="product-announcement-mobile-trigger__icon" aria-hidden="true" />
          {hasUnread ? <span className="product-announcement-bell__dot" aria-hidden="true" /> : null}
        </button>
        <AgentChatMobileSheet
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          title="Updates"
          icon={Bell}
          tone="sidebar"
          bodyPadding={false}
          ariaLabel="Product updates"
        >
          {panel}
        </AgentChatMobileSheet>
      </>
    )
  }

  return (
    <DialogTrigger>
      <Button className="chat-sidebar-toggle product-announcement-bell__trigger" aria-label={label}>
        <Bell className="h-4 w-4" aria-hidden="true" />
        {hasUnread ? <span className="product-announcement-bell__dot" aria-hidden="true" /> : null}
      </Button>
      <Popover
        className="product-announcement-bell__popover sidebar-settings__popover"
        placement="bottom end"
        offset={10}
        isNonModal
      >
        <Dialog className="product-announcement-bell__menu sidebar-settings__menu" aria-label="Product updates">
          {panel}
        </Dialog>
      </Popover>
    </DialogTrigger>
  )
}
