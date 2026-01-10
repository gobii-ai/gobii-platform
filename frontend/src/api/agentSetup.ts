import { jsonFetch, jsonRequest } from './http'

export type PhoneState = {
  number: string
  isVerified: boolean
  verifiedAt: string | null
  cooldownRemaining: number
}

export type PhoneResponse = {
  phone: PhoneState | null
}

export type EnableSmsResponse = {
  agentSms: { number: string } | null
  userPhone: PhoneState | null
  preferredContactMethod: 'sms'
}

export type ReassignResponse = {
  success: boolean
  context?: { type: 'personal' | 'organization'; id: string; name: string }
  switch?: { type: 'personal' | 'organization'; id: string }
  redirect?: string
  organization?: { id: string; name: string } | null
  error?: string
}

export function fetchUserPhone(): Promise<PhoneResponse> {
  return jsonFetch<PhoneResponse>('/console/api/user/phone/')
}

export function addUserPhone(phoneNumber: string): Promise<PhoneResponse> {
  return jsonRequest<PhoneResponse>('/console/api/user/phone/', {
    method: 'POST',
    json: { phone_number: phoneNumber },
    includeCsrf: true,
  })
}

export function deleteUserPhone(): Promise<PhoneResponse> {
  return jsonRequest<PhoneResponse>('/console/api/user/phone/', {
    method: 'DELETE',
    includeCsrf: true,
  })
}

export function verifyUserPhone(code: string): Promise<PhoneResponse> {
  return jsonRequest<PhoneResponse>('/console/api/user/phone/verify/', {
    method: 'POST',
    json: { verification_code: code },
    includeCsrf: true,
  })
}

export function resendUserPhone(): Promise<PhoneResponse> {
  return jsonRequest<PhoneResponse>('/console/api/user/phone/resend/', {
    method: 'POST',
    json: {},
    includeCsrf: true,
  })
}

export function enableAgentSms(agentId: string): Promise<EnableSmsResponse> {
  return jsonRequest<EnableSmsResponse>(`/console/api/agents/${agentId}/sms/enable/`, {
    method: 'POST',
    json: {},
    includeCsrf: true,
  })
}

export function reassignAgentOrg(agentId: string, targetOrgId: string | null): Promise<ReassignResponse> {
  return jsonRequest<ReassignResponse>(`/console/api/agents/${agentId}/reassign/`, {
    method: 'POST',
    json: { target_org_id: targetOrgId },
    includeCsrf: true,
  })
}
