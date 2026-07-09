import type { PhoneState } from './agentSetup'
import type { SupportedPhoneRegion } from '../components/common/PhoneNumberInput'
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
  customInstructions: string
  customInstructionsMaxChars: number
  referralLink: string
  emailVerification: EmailVerificationState
  phone: PhoneState | null
  pendingPhone?: PhoneState | null
  supportedPhoneRegions: SupportedPhoneRegion[]
}

export type UserProfileErrorPayload = {
  errors?: Partial<Record<keyof UserProfileFormState | 'profile' | 'customInstructions' | 'nonFieldErrors', string[]>>
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

export function updateUserCustomInstructions(customInstructions: string): Promise<UserProfilePayload> {
  return jsonRequest<UserProfilePayload>('/console/api/user/profile/', {
    method: 'PATCH',
    json: { customInstructions },
    includeCsrf: true,
  })
}
