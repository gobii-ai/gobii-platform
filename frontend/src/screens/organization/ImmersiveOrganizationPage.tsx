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
    <div className={layout === 'sidebar-shell' ? 'w-full px-1 pb-4' : 'mx-auto w-full max-w-5xl px-4 pb-6'}>
      <OrganizationScreen key={refreshKey} />
    </div>
  )
}
