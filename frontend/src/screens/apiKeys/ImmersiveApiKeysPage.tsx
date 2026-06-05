import { ImmersivePageFrame } from '../../components/common/ImmersivePageFrame'
import { ApiKeysScreen } from '../ApiKeysScreen'

type ImmersiveApiKeysPageProps = {
  refreshKey?: number
  layout?: 'main' | 'sidebar-shell'
}

export function ImmersiveApiKeysPage({
  refreshKey = 0,
  layout = 'main',
}: ImmersiveApiKeysPageProps) {
  return (
    <ImmersivePageFrame layout={layout}>
      <ApiKeysScreen key={refreshKey} />
    </ImmersivePageFrame>
  )
}
