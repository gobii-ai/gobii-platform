import { ImmersivePageFrame } from '../../components/common/ImmersivePageFrame'
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
    <ImmersivePageFrame layout={layout}>
      <UsageScreen key={refreshKey} />
    </ImmersivePageFrame>
  )
}
