import type { PhoneState } from './agentSetup'
import { jsonFetch, jsonRequest } from './http'

export type UserProfileFormState = {
  firstName: string
  lastName: string
  timezone: string
}

export type TimezoneOption = {
  value: string
  label: string
}

export type EmailVerificationState = {
  email: string
  isVerified: boolean
}

export type UserProfilePayload = {
  profile: UserProfileFormState
  timezoneOptions: TimezoneOption[]
  referralLink: string
  emailVerification: EmailVerificationState
  phone: PhoneState | null
}

export type UserProfileErrorPayload = {
  errors?: Partial<Record<keyof UserProfileFormState | 'profile' | 'nonFieldErrors', string[]>>
}

export function fetchUserProfile(signal?: AbortSignal): Promise<UserProfilePayload> {
  return jsonFetch<UserProfilePayload>('/console/api/user/profile/', { signal })
}

export function updateUserProfile(profile: UserProfileFormState): Promise<UserProfilePayload> {
  return jsonRequest<UserProfilePayload>('/console/api/user/profile/', {
    method: 'PATCH',
    json: { profile },
    includeCsrf: true,
  })
}
