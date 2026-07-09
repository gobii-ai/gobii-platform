import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Bell, Check, ChevronLeft, Loader2 } from 'lucide-react'
import { Button, Dialog, DialogTrigger, Popover } from 'react-aria-components'

import type { ProductAnnouncement } from '../../api/productAnnouncements'
import { useMarkProductAnnouncementsRead, useProductAnnouncements } from '../../hooks/useProductAnnouncements'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import { AgentChatButton, AgentChatIconButton, AgentChatMenuItem, joinClassNames } from './uiPrimitives'

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
  selectedAnnouncement: ProductAnnouncement | null
  onRetry: () => void
  onBackToList: () => void
  onOpenAnnouncement: (announcement: ProductAnnouncement) => void
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
  selectedAnnouncement,
  onRetry,
  onBackToList,
  onOpenAnnouncement,
  onMarkAllRead,
  onMarkRead,
  onAction,
}: AnnouncementPanelProps) {
  if (selectedAnnouncement) {
    return (
      <div className="product-announcement-panel" data-variant={variant}>
        <div className="product-announcement-panel__detail-header">
          <AgentChatButton
            variant="soft"
            size="sm"
            className="product-announcement-panel__back"
            onClick={onBackToList}
            aria-label="Back to updates"
          >
            <ChevronLeft className="product-announcement-panel__back-icon" aria-hidden="true" />
            <span>Updates</span>
          </AgentChatButton>
          {selectedAnnouncement.publishedAt ? (
            <time className="product-announcement-panel__item-date" dateTime={selectedAnnouncement.publishedAt}>
              {formatPublishedAt(selectedAnnouncement.publishedAt)}
            </time>
          ) : null}
        </div>
        <div className="product-announcement-panel__detail">
          <h2 className="product-announcement-panel__detail-title">{selectedAnnouncement.title}</h2>
          <p className="product-announcement-panel__detail-body">{selectedAnnouncement.body}</p>
          {selectedAnnouncement.actionLabel && selectedAnnouncement.actionUrl ? (
            <AgentChatButton
              size="sm"
              className="product-announcement-panel__action"
              onClick={() => onAction(selectedAnnouncement)}
              disabled={markBusy}
            >
              {selectedAnnouncement.actionLabel}
            </AgentChatButton>
          ) : null}
        </div>
      </div>
    )
  }

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
          <AgentChatButton
            size="sm"
            className="product-announcement-panel__mark-all"
            onClick={onMarkAllRead}
            disabled={markBusy}
          >
            {markBusy ? <Loader2 className="product-announcement-panel__spinner animate-spin" aria-hidden="true" /> : null}
            <span>Mark all read</span>
          </AgentChatButton>
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
          <AgentChatButton size="sm" className="product-announcement-panel__retry" onClick={onRetry}>
            Try again
          </AgentChatButton>
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
              <AgentChatMenuItem
                className="product-announcement-panel__item-main"
                onClick={() => onOpenAnnouncement(announcement)}
                disabled={markBusy}
                aria-label={`Open "${announcement.title}"`}
              >
                <div className="product-announcement-panel__item-copy">
                  <div className="product-announcement-panel__item-header">
                    <h3 className="product-announcement-panel__item-title">{announcement.title}</h3>
                    {announcement.publishedAt ? (
                      <time className="product-announcement-panel__item-date" dateTime={announcement.publishedAt}>
                        {formatPublishedAt(announcement.publishedAt)}
                      </time>
                    ) : null}
                  </div>
                  <p className="product-announcement-panel__preview">{announcement.body}</p>
                </div>
              </AgentChatMenuItem>
              {!announcement.isRead ? (
                <AgentChatIconButton
                  tone="success"
                  size="sm"
                  className="product-announcement-panel__read-button"
                  aria-label={`Mark "${announcement.title}" as read`}
                  onClick={() => onMarkRead(announcement.id)}
                  disabled={markBusy}
                >
                  <Check className="product-announcement-panel__read-icon" aria-hidden="true" />
                </AgentChatIconButton>
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
  const [desktopOpen, setDesktopOpen] = useState(false)
  const [selectedAnnouncementId, setSelectedAnnouncementId] = useState<string | null>(null)
  const desktopTriggerRef = useRef<HTMLButtonElement | null>(null)
  const desktopPopoverRef = useRef<HTMLElement | null>(null)
  const announcementsQuery = useProductAnnouncements()
  const markReadMutation = useMarkProductAnnouncementsRead()
  const announcements = announcementsQuery.data?.announcements ?? []
  const unreadCount = announcementsQuery.data?.unreadCount ?? 0
  const hasUnread = announcementsQuery.data?.hasUnread ?? false
  const label = useMemo(() => resolveUnreadLabel(unreadCount), [unreadCount])
  const markBusy = markReadMutation.isPending
  const selectedAnnouncement = useMemo(
    () => announcements.find((announcement) => announcement.id === selectedAnnouncementId) ?? null,
    [announcements, selectedAnnouncementId],
  )
  useEffect(() => {
    if (!desktopOpen || typeof document === 'undefined') {
      return
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Node)) {
        return
      }
      if (desktopTriggerRef.current?.contains(target) || desktopPopoverRef.current?.contains(target)) {
        return
      }
      setDesktopOpen(false)
    }

    document.addEventListener('pointerdown', handlePointerDown, true)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true)
    }
  }, [desktopOpen])

  const handleMarkAllRead = useCallback(() => {
    if (unreadCount <= 0) {
      return
    }
    markReadMutation.mutate({ all: true })
  }, [markReadMutation, unreadCount])

  const handleMarkRead = useCallback((announcementId: string) => {
    markReadMutation.mutate({ announcementIds: [announcementId] })
  }, [markReadMutation])

  const handleOpenAnnouncement = useCallback((announcement: ProductAnnouncement) => {
    setSelectedAnnouncementId(announcement.id)
    if (!announcement.isRead) {
      markReadMutation.mutate({ announcementIds: [announcement.id] })
    }
  }, [markReadMutation])

  const handleAction = useCallback((announcement: ProductAnnouncement) => {
    const actionUrl = announcement.actionUrl
    if (!actionUrl || typeof window === 'undefined') {
      return
    }
    if (!announcement.isRead) {
      markReadMutation.mutate({ announcementIds: [announcement.id] })
    }
    if (actionUrl.startsWith('/')) {
      window.location.assign(actionUrl)
      return
    }
    window.open(actionUrl, '_blank', 'noopener,noreferrer')
  }, [markReadMutation])

  const handleDesktopTriggerPointerDownCapture = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!desktopOpen) {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    setDesktopOpen(false)
  }, [desktopOpen])

  const panel = (
    <AnnouncementPanel
      announcements={announcements}
      loading={announcementsQuery.isLoading}
      error={announcementsQuery.isError}
      unreadCount={unreadCount}
      markBusy={markBusy}
      variant={variant}
      selectedAnnouncement={selectedAnnouncement}
      onRetry={() => void announcementsQuery.refetch()}
      onBackToList={() => setSelectedAnnouncementId(null)}
      onOpenAnnouncement={handleOpenAnnouncement}
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
          className={joinClassNames(
            'sidebar-settings__trigger',
            'product-announcement-bell__trigger',
            'product-announcement-bell__trigger--settings',
          )}
          aria-label={label}
          onClick={() => setMobileOpen(true)}
        >
          <Bell className="h-4 w-4" aria-hidden="true" />
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
    <DialogTrigger isOpen={desktopOpen} onOpenChange={setDesktopOpen}>
      <Button
        ref={desktopTriggerRef}
        className={joinClassNames(
          'sidebar-settings__trigger',
          'product-announcement-bell__trigger',
          'product-announcement-bell__trigger--settings',
        )}
        aria-label={label}
        onPointerDownCapture={handleDesktopTriggerPointerDownCapture}
      >
        <Bell className="h-4 w-4" aria-hidden="true" />
        {hasUnread ? <span className="product-announcement-bell__dot" aria-hidden="true" /> : null}
      </Button>
      <Popover
        ref={desktopPopoverRef}
        triggerRef={desktopTriggerRef}
        className="product-announcement-bell__popover sidebar-settings__popover"
        placement="top end"
        offset={10}
        shouldCloseOnInteractOutside={() => true}
        isNonModal
      >
        <Dialog className="product-announcement-bell__menu sidebar-settings__menu" aria-label="Product updates">
          {panel}
        </Dialog>
      </Popover>
    </DialogTrigger>
  )
}
