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
    <div className={layout === 'sidebar-shell' ? 'w-full px-1 pb-4' : 'mx-auto w-full max-w-5xl px-4 pb-6'}>
      <ApiKeysScreen key={refreshKey} />
    </div>
  )
}
