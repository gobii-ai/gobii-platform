import { ImmersivePageFrame } from '../../components/common/ImmersivePageFrame'
import { OrganizationScreen } from '../OrganizationScreen'

type ImmersiveOrganizationPageProps = {
  refreshKey?: number
  layout?: 'main' | 'sidebar-shell'
}

export function ImmersiveOrganizationPage({
  refreshKey = 0,
  layout = 'main',
}: ImmersiveOrganizationPageProps) {
  return (
    <ImmersivePageFrame layout={layout}>
      <OrganizationScreen key={refreshKey} />
    </ImmersivePageFrame>
  )
}
