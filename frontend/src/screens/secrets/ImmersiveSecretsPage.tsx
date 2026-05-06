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
    <div className={layout === 'sidebar-shell' ? 'w-full px-1 pb-4' : 'mx-auto w-full max-w-5xl px-4 pb-6'}>
      <GlobalSecretsScreen
        key={refreshKey}
        listUrl="/console/api/secrets/"
        variant="embedded"
      />
    </div>
  )
}
