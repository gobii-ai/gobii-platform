import { jsonFetch, jsonRequest } from './http'

export type ProductAnnouncement = {
  id: string
  title: string
  body: string
  actionLabel: string | null
  actionUrl: string | null
  publishedAt: string | null
  readAt: string | null
  isRead: boolean
}

export type ProductAnnouncementsPayload = {
  announcements: ProductAnnouncement[]
  unreadCount: number
  hasUnread: boolean
  recentLimit: number
}

export type ProductAnnouncementReadPayload =
  | { all: true }
  | { announcementIds: string[] }

export function fetchProductAnnouncements(): Promise<ProductAnnouncementsPayload> {
  return jsonFetch<ProductAnnouncementsPayload>('/console/api/product-announcements/')
}

export function markProductAnnouncementsRead(
  payload: ProductAnnouncementReadPayload,
): Promise<ProductAnnouncementsPayload> {
  return jsonRequest<ProductAnnouncementsPayload>('/console/api/product-announcements/read/', {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}
