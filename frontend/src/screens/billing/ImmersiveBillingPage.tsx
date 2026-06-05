import { useQuery } from '@tanstack/react-query'

import { jsonFetch } from '../../api/http'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { ImmersivePageFrame } from '../../components/common/ImmersivePageFrame'
import { BillingScreen } from './BillingScreen'
import type { BillingInitialData } from './types'

type ImmersiveBillingPageProps = {
  refreshKey?: number
  layout?: 'main' | 'sidebar-shell'
}

export function ImmersiveBillingPage({
  refreshKey = 0,
  layout = 'main',
}: ImmersiveBillingPageProps) {
  const { data, isLoading, error } = useQuery<BillingInitialData, Error>({
    queryKey: ['billing-initial-data', 'immersive', refreshKey],
    queryFn: ({ signal }) => jsonFetch<BillingInitialData>('/console/api/billing/initial/', { signal }),
    staleTime: 0,
    refetchOnWindowFocus: false,
  })

  if (isLoading) {
    return (
      <ImmersivePageFrame layout={layout} loading loadingLabel="Loading billing…" />
    )
  }

  if (!data) {
    return (
      <ImmersivePageFrame
        layout={layout}
        error={safeErrorMessage(error) || 'Unable to load billing right now.'}
      />
    )
  }

  return (
    <ImmersivePageFrame layout={layout}>
      <BillingScreen initialData={data} />
    </ImmersivePageFrame>
  )
}
