import { jsonFetch, jsonRequest } from './http'

export type StaffUserSearchResult = {
  id: number
  name: string
  email: string
}

export type StaffUserDetail = {
  user: {
    id: number
    name: string
    email: string
    adminUrl: string
  }
  emailVerification: {
    email: string
    isVerified: boolean
  }
  billing: {
    plan: {
      id: string
      name: string
    }
    stripeCustomerId: string | null
    stripeCustomerUrl: string | null
    addons: Array<{
      id: string
      kind: string
      label: string
      quantity: number
      priceId: string
      summary: string
      startsAt: string | null
      expiresAt: string | null
      isRecurring: boolean
    }>
  }
  agents: Array<{
    id: string
    name: string
    organizationName: string | null
    adminUrl: string
    auditUrl: string
  }>
  taskCredits: {
    available: number | null
    unlimited: boolean
    recentGrants: Array<{
      id: string
      credits: number
      used: number
      available: number
      grantType: string
      grantedAt: string
      expiresAt: string
      comments: string
    }>
  }
}

export type StaffUserEmailVerification = StaffUserDetail['emailVerification']
export type StaffUserTaskCreditGrant = StaffUserDetail['taskCredits']['recentGrants'][number]

export async function searchStaffUsers(query: string, limit = 8, signal?: AbortSignal): Promise<{ users: StaffUserSearchResult[] }> {
  const params = new URLSearchParams()
  params.set('q', query)
  params.set('limit', String(limit))
  return jsonFetch<{ users: StaffUserSearchResult[] }>(`/console/api/staff/users/search/?${params.toString()}`, { signal })
}

export async function fetchStaffUserDetail(userId: number, signal?: AbortSignal): Promise<StaffUserDetail> {
  return jsonFetch<StaffUserDetail>(`/console/api/staff/users/${userId}/`, { signal })
}

export async function markStaffUserEmailVerified(userId: number): Promise<{ ok: boolean; emailVerification: StaffUserEmailVerification }> {
  return jsonRequest<{ ok: boolean; emailVerification: StaffUserEmailVerification }>(`/console/api/staff/users/${userId}/email/verify/`, {
    method: 'POST',
    includeCsrf: true,
  })
}

export async function createStaffUserTaskCreditGrant(
  userId: number,
  payload: { credits: string; grantType: 'Compensation' | 'Promo'; expirationPreset: 'one_month' | 'one_year' },
): Promise<{ ok: boolean; taskCredit: StaffUserTaskCreditGrant }> {
  return jsonRequest<{ ok: boolean; taskCredit: StaffUserTaskCreditGrant }>(`/console/api/staff/users/${userId}/task-credits/`, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}
