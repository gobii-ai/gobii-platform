import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  deleteUserPet,
  fetchUserPets,
  updateUserPet,
  updateUserPetPreferences,
  uploadUserPet,
  type UserPetLibrary,
  type UserPetPreferencesPatch,
} from '../api/userPets'

export const USER_PETS_QUERY_KEY = ['user-pets'] as const

export function useUserPets(enabled = true) {
  return useQuery({
    queryKey: USER_PETS_QUERY_KEY,
    queryFn: ({ signal }) => fetchUserPets(signal),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    enabled,
  })
}

function useUserPetLibraryMutation<TVariables>(
  mutationFn: (variables: TVariables) => Promise<UserPetLibrary>,
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: (data) => queryClient.setQueryData(USER_PETS_QUERY_KEY, data),
  })
}

export function useUpdateUserPetPreferences() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: updateUserPetPreferences,
    onMutate: async (patch: UserPetPreferencesPatch) => {
      await queryClient.cancelQueries({ queryKey: USER_PETS_QUERY_KEY })
      const previous = queryClient.getQueryData<UserPetLibrary>(USER_PETS_QUERY_KEY)
      if (previous) {
        queryClient.setQueryData<UserPetLibrary>(USER_PETS_QUERY_KEY, {
          ...previous,
          preferences: {
            ...previous.preferences,
            ...patch,
          },
        })
      }
      return { previous }
    },
    onError: (_error, _patch, context) => {
      if (context?.previous) {
        queryClient.setQueryData(USER_PETS_QUERY_KEY, context.previous)
      }
    },
    onSuccess: (data) => {
      queryClient.setQueryData(USER_PETS_QUERY_KEY, data)
    },
  })
}

export function useUploadUserPet() {
  return useUserPetLibraryMutation(uploadUserPet)
}

export function useUpdateUserPet() {
  return useUserPetLibraryMutation(({
    petId,
    changes,
  }: {
    petId: string
    changes: { displayName?: string; description?: string }
  }) => updateUserPet(petId, changes))
}

export function useDeleteUserPet() {
  return useUserPetLibraryMutation(deleteUserPet)
}
