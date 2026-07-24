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

export function useUserPets() {
  return useQuery({
    queryKey: USER_PETS_QUERY_KEY,
    queryFn: ({ signal }) => fetchUserPets(signal),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
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
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: uploadUserPet,
    onSuccess: (data) => queryClient.setQueryData(USER_PETS_QUERY_KEY, data),
  })
}

export function useUpdateUserPet() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ petId, changes }: {
      petId: string
      changes: { displayName?: string; description?: string }
    }) => updateUserPet(petId, changes),
    onSuccess: (data) => queryClient.setQueryData(USER_PETS_QUERY_KEY, data),
  })
}

export function useDeleteUserPet() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteUserPet,
    onSuccess: (data) => queryClient.setQueryData(USER_PETS_QUERY_KEY, data),
  })
}
