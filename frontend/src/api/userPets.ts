import { jsonFetch, jsonRequest } from './http'

export type UserPetSize = 'small' | 'medium' | 'large'

export type UserPetPosition = {
  x: number
  y: number
}

export type UserPet = {
  id: string
  kind: 'builtin' | 'custom'
  displayName: string
  description: string
  spritesheetUrl: string
}

export type UserPetPreferences = {
  enabled: boolean
  selectedPetId: string
  size: UserPetSize
  position: UserPetPosition | null
}

export type UserPetLibrary = {
  pets: UserPet[]
  preferences: UserPetPreferences
  maxCustomPets: number
}

export type UserPetPreferencesPatch = Partial<UserPetPreferences>

export function getSelectedUserPet(library?: UserPetLibrary | null): UserPet | null {
  return library?.pets.find((pet) => pet.id === library.preferences.selectedPetId)
    ?? library?.pets[0]
    ?? null
}

export function fetchUserPets(signal?: AbortSignal): Promise<UserPetLibrary> {
  return jsonFetch<UserPetLibrary>('/console/api/user/pets/', { signal })
}

export function updateUserPetPreferences(
  preferences: UserPetPreferencesPatch,
): Promise<UserPetLibrary> {
  return jsonRequest<UserPetLibrary>('/console/api/user/pets/', {
    method: 'PATCH',
    json: preferences,
    includeCsrf: true,
  })
}

export function uploadUserPet({
  displayName,
  description,
  spritesheet,
}: {
  displayName: string
  description: string
  spritesheet: File
}): Promise<UserPetLibrary> {
  const formData = new FormData()
  formData.append('displayName', displayName)
  formData.append('description', description)
  formData.append('spritesheet', spritesheet)
  return jsonRequest<UserPetLibrary>('/console/api/user/pets/', {
    method: 'POST',
    body: formData,
    includeCsrf: true,
  })
}

export function updateUserPet(
  petId: string,
  changes: { displayName?: string; description?: string },
): Promise<UserPetLibrary> {
  return jsonRequest<UserPetLibrary>(`/console/api/user/pets/${petId}/`, {
    method: 'PATCH',
    json: changes,
    includeCsrf: true,
  })
}

export function deleteUserPet(petId: string): Promise<UserPetLibrary> {
  return jsonRequest<UserPetLibrary>(`/console/api/user/pets/${petId}/`, {
    method: 'DELETE',
    includeCsrf: true,
  })
}
