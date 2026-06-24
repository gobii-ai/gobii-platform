import { jsonFetch, jsonRequest } from './http'

export type StaffUserSearchResult = {
  id: number
  name: string
  email: string
}

export type StaffOrganizationSearchResult = {
  id: string
  name: string
  slug: string
}

export type StaffAgentSummary = {
  id: string
  name: string
  organizationName: string | null
  adminUrl: string
  auditUrl: string
  lastInteractionAt: string | null
}

export type StaffTaskCreditGrant = {
  id: string
  credits: string
  used: string
  available: string
  grantType: string
  grantedAt: string
  expiresAt: string
  comments: string
}

export type StaffTaskCredits = {
  available: string | null
  unlimited: boolean
  recentGrants: StaffTaskCreditGrant[]
}

export type StaffTaskCreditGrantPayload = {
  credits: string
  grantType: 'Compensation' | 'Promo'
  expirationPreset: 'one_month' | 'one_year'
}

export type StaffScopedSystemMessagePayload = {
  body: string
}

export type StaffScopedSystemMessageResponse = {
  ok: boolean
  createdCount: number
  targetCount: number
}

export type StaffScopedProcessEventsResponse = {
  ok: boolean
  queuedCount: number
  skippedInactiveCount: number
  targetCount: number
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
  agents: StaffAgentSummary[]
  userEmails: {
    triggers: Array<{
      id: number
      name: string
      eventName: string
    }>
  }
  taskCredits: StaffTaskCredits
}

export type StaffOrgDetail = {
  organization: {
    id: string
    name: string
    slug: string
    plan: string
    isActive: boolean
    adminUrl: string
    createdAt: string | null
  }
  billing: {
    subscription: string | null
    purchasedSeats: number | null
    seatsReserved: number | null
    seatsAvailable: number | null
  }
  members: Array<{
    userId: number
    name: string
    email: string
    role: string
    roleLabel: string
    adminUrl: string
  }>
  agents: StaffAgentSummary[]
  taskCredits: StaffTaskCredits
}

export type StaffSearchResults = {
  users: StaffUserSearchResult[]
  organizations: StaffOrganizationSearchResult[]
}

export type StaffUserEmailVerification = StaffUserDetail['emailVerification']
export type StaffUserEmailTrigger = StaffUserDetail['userEmails']['triggers'][number]

export async function searchStaffUsers(query: string, limit = 8, signal?: AbortSignal): Promise<StaffSearchResults> {
  const params = new URLSearchParams()
  params.set('q', query)
  params.set('limit', String(limit))
  const payload = await jsonFetch<Partial<StaffSearchResults>>(`/console/api/staff/users/search/?${params.toString()}`, { signal })
  return {
    users: payload.users ?? [],
    organizations: payload.organizations ?? [],
  }
}

export async function fetchStaffUserDetail(userId: number, signal?: AbortSignal): Promise<StaffUserDetail> {
  return jsonFetch<StaffUserDetail>(`/console/api/staff/users/${userId}/`, { signal })
}

export async function fetchStaffOrgDetail(orgId: string, signal?: AbortSignal): Promise<StaffOrgDetail> {
  return jsonFetch<StaffOrgDetail>(`/console/api/staff/orgs/${orgId}/`, { signal })
}

export async function markStaffUserEmailVerified(userId: number): Promise<{ ok: boolean; emailVerification: StaffUserEmailVerification }> {
  return jsonRequest<{ ok: boolean; emailVerification: StaffUserEmailVerification }>(`/console/api/staff/users/${userId}/email/verify/`, {
    method: 'POST',
    includeCsrf: true,
  })
}

function createStaffTaskCreditGrant(url: string, payload: StaffTaskCreditGrantPayload): Promise<{ ok: boolean; taskCredit: StaffTaskCreditGrant }> {
  return jsonRequest<{ ok: boolean; taskCredit: StaffTaskCreditGrant }>(url, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}

export function createStaffUserTaskCreditGrant(userId: number, payload: StaffTaskCreditGrantPayload) {
  return createStaffTaskCreditGrant(`/console/api/staff/users/${userId}/task-credits/`, payload)
}

export function createStaffOrgTaskCreditGrant(orgId: string, payload: StaffTaskCreditGrantPayload) {
  return createStaffTaskCreditGrant(`/console/api/staff/orgs/${orgId}/task-credits/`, payload)
}

function createStaffScopedSystemMessage(url: string, payload: StaffScopedSystemMessagePayload): Promise<StaffScopedSystemMessageResponse> {
  return jsonRequest<StaffScopedSystemMessageResponse>(url, {
    method: 'POST',
    includeCsrf: true,
    json: payload,
  })
}

export function createStaffUserSystemMessage(userId: number, payload: StaffScopedSystemMessagePayload) {
  return createStaffScopedSystemMessage(`/console/api/staff/users/${userId}/system-messages/`, payload)
}

export function createStaffOrgSystemMessage(orgId: string, payload: StaffScopedSystemMessagePayload) {
  return createStaffScopedSystemMessage(`/console/api/staff/orgs/${orgId}/system-messages/`, payload)
}

function triggerStaffScopedProcessEvents(url: string): Promise<StaffScopedProcessEventsResponse> {
  return jsonRequest<StaffScopedProcessEventsResponse>(url, {
    method: 'POST',
    includeCsrf: true,
  })
}

export function triggerStaffUserProcessEvents(userId: number) {
  return triggerStaffScopedProcessEvents(`/console/api/staff/users/${userId}/process-events/`)
}

export function triggerStaffOrgProcessEvents(orgId: string) {
  return triggerStaffScopedProcessEvents(`/console/api/staff/orgs/${orgId}/process-events/`)
}

export async function sendStaffUserEmailTrigger(
  userId: number,
  userEmailId: number,
): Promise<{ ok: boolean; userEmail: StaffUserEmailTrigger }> {
  return jsonRequest<{ ok: boolean; userEmail: StaffUserEmailTrigger }>(
    `/console/api/staff/users/${userId}/user-emails/${userEmailId}/send/`,
    {
      method: 'POST',
      includeCsrf: true,
    },
  )
}
