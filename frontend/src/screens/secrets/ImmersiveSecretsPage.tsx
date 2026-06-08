import { ImmersivePageFrame } from '../../components/common/ImmersivePageFrame'
import { GlobalSecretsScreen } from '../GlobalSecretsScreen'

type ImmersiveSecretsPageProps = {
  layout?: 'main' | 'sidebar-shell'
  refreshKey?: number
}

export function ImmersiveSecretsPage({
  layout = 'main',
  refreshKey = 0,
}: ImmersiveSecretsPageProps) {
  return (
    <ImmersivePageFrame layout={layout}>
      <GlobalSecretsScreen
        key={refreshKey}
        listUrl="/console/api/secrets/"
      />
    </ImmersivePageFrame>
  )
}
