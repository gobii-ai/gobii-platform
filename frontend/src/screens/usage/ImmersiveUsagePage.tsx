import { UsageScreen } from '../UsageScreen'

type ImmersiveUsagePageProps = {
  refreshKey?: number
  layout?: 'main' | 'sidebar-shell'
}

export function ImmersiveUsagePage({
  refreshKey = 0,
  layout = 'main',
}: ImmersiveUsagePageProps) {
  return (
    <div className={layout === 'sidebar-shell' ? 'w-full px-1 pb-4' : 'mx-auto w-full max-w-5xl px-4 pb-6'}>
      <UsageScreen key={refreshKey} variant="embedded" />
    </div>
  )
}
