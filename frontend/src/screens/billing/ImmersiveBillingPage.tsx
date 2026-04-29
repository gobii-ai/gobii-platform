import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'

import { jsonFetch } from '../../api/http'
import { safeErrorMessage } from '../../api/safeErrorMessage'
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
      <div className={layout === 'sidebar-shell'
        ? 'flex min-h-[24rem] w-full items-center justify-center px-1 pb-4'
        : 'mx-auto flex min-h-[40vh] w-full max-w-5xl items-center justify-center px-4 pb-6'}
      >
        <p className="text-sm font-medium text-slate-300">Loading billing…</p>
      </div>
    )
  }

  if (!data) {
    return (
      <div className={layout === 'sidebar-shell' ? 'w-full px-1 pb-4' : 'mx-auto w-full max-w-5xl px-4 pb-6'}>
        <section
          className="flex items-start gap-3 rounded-3xl border border-rose-300/30 bg-rose-500/10 px-5 py-4 text-sm text-rose-100 backdrop-blur-xl"
          role="alert"
        >
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
          <div>{safeErrorMessage(error) || 'Unable to load billing right now.'}</div>
        </section>
      </div>
    )
  }

  return (
    <div className={layout === 'sidebar-shell' ? 'w-full px-1 pb-4' : 'mx-auto w-full max-w-5xl px-4 pb-6'}>
      <BillingScreen initialData={data} variant="embedded" />
    </div>
  )
}
