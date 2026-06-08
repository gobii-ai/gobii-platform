import { useQuery } from '@tanstack/react-query'

import { safeErrorMessage } from '../../api/safeErrorMessage'
import { fetchUserProfile } from '../../api/userProfile'
import type { UserProfilePayload } from '../../api/userProfile'
import { ImmersivePageFrame } from '../../components/common/ImmersivePageFrame'
import { ProfileScreen } from './ProfileScreen'

type ImmersiveProfilePageProps = {
  refreshKey?: number
  layout?: 'main' | 'sidebar-shell'
}

export function ImmersiveProfilePage({
  refreshKey = 0,
  layout = 'main',
}: ImmersiveProfilePageProps) {
  const { data, isLoading, error } = useQuery<UserProfilePayload, Error>({
    queryKey: ['user-profile', 'immersive', refreshKey],
    queryFn: ({ signal }) => fetchUserProfile(signal),
    staleTime: 0,
    refetchOnWindowFocus: false,
  })

  if (isLoading) {
    return (
      <ImmersivePageFrame layout={layout} loading loadingLabel="Loading profile..." />
    )
  }

  if (!data) {
    return (
      <ImmersivePageFrame
        layout={layout}
        error={safeErrorMessage(error) || 'Unable to load profile right now.'}
      />
    )
  }

  return (
    <ImmersivePageFrame layout={layout}>
      <ProfileScreen initialData={data} />
    </ImmersivePageFrame>
  )
}
