import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  fetchProductAnnouncements,
  markProductAnnouncementsRead,
  type ProductAnnouncementReadPayload,
  type ProductAnnouncementsPayload,
} from '../api/productAnnouncements'

export const productAnnouncementsQueryKey = ['product-announcements'] as const

export function useProductAnnouncements(enabled = true) {
  return useQuery({
    queryKey: productAnnouncementsQueryKey,
    queryFn: fetchProductAnnouncements,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
    enabled,
  })
}

export function useMarkProductAnnouncementsRead() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (payload: ProductAnnouncementReadPayload) => markProductAnnouncementsRead(payload),
    onSuccess: (payload: ProductAnnouncementsPayload) => {
      queryClient.setQueryData(productAnnouncementsQueryKey, payload)
    },
  })
}
