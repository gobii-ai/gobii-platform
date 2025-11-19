import { useCallback, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowLeft,
  ArrowUpFromLine,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  Info,
  KeyRound,
  Mail,
  MessageSquare,
  Phone,
  Plus,
  ServerCog,
  ShieldAlert,
  Trash2,
  X,
  XCircle,
} from 'lucide-react'

type PrimaryEndpoint = {
  address: string
}

type PendingTransfer = {
  toEmail: string
  createdAtIso: string
  createdAtDisplay: string
}

type AgentOrganization = {
  id: string
  name: string
} | null

type AgentSummary = {
  id: string
  name: string
  charter: string
  isActive: boolean
  createdAtDisplay: string
  pendingTransfer: PendingTransfer | null
  whitelistPolicy: string
  organization: AgentOrganization
}

type DailyCreditsInfo = {
  limit: number | null
  hardLimit: number | null
  usage: number
  remaining: number | null
  softRemaining: number | null
  unlimited: boolean
  percentUsed: number | null
  softPercentUsed: number | null
  nextResetIso: string | null
  nextResetLabel: string | null
  low: boolean
  sliderMin: number
  sliderMax: number
  sliderStep: number
  sliderValue: number
  sliderEmptyValue: number
}

type DedicatedIpOption = {
  id: string
  label: string
  inUseElsewhere: boolean
  disabled: boolean
  assignedNames: string[]
}

type DedicatedIpInfo = {
  total: number
  available: number
  multiAssign: boolean
  ownerType: 'organization' | 'user'
  selectedId: string | null
  options: DedicatedIpOption[]
  organizationName: string | null
}

type AllowlistEntry = {
  id: string
  channel: string
  address: string
  allowInbound: boolean
  allowOutbound: boolean
}

type AllowlistInvite = {
  id: string
  channel: string
  address: string
  allowInbound: boolean
  allowOutbound: boolean
}

type AllowlistState = {
  show: boolean
  ownerEmail: string | null
  ownerPhone: string | null
  entries: AllowlistEntry[]
  pendingInvites: AllowlistInvite[]
  activeCount: number
  maxContacts: number | null
  pendingContactRequests: number
}

type McpServer = {
  id: string
  displayName: string
  description: string | null
  scope: string
  inherited: boolean
  assigned: boolean
}

type PersonalMcpServer = {
  id: string
  displayName: string
  description: string | null
  assigned: boolean
}

type McpServersInfo = {
  inherited: McpServer[]
  personal: PersonalMcpServer[]
  showPersonalForm: boolean
  canManage: boolean
  manageUrl: string | null
}

type PeerLinkCandidate = {
  id: string
  name: string
}

type PeerLinkState = {
  creditsRemaining: number | null
  windowResetLabel: string | null
}

type PeerLinkEntry = {
  id: string
  counterpartId: string | null
  counterpartName: string | null
  isEnabled: boolean
  messagesPerWindow: number
  windowHours: number
  featureFlag: string | null
  createdOnLabel: string
  state: PeerLinkState | null
}

type PeerLinksInfo = {
  entries: PeerLinkEntry[]
  candidates: PeerLinkCandidate[]
  defaults: {
    messagesPerWindow: number
    windowHours: number
  }
}

type AgentWebhook = {
  id: string
  name: string
  url: string
}

type ReassignmentInfo = {
  enabled: boolean
  canReassign: boolean
  organizations: { id: string; name: string }[]
  assignedOrg: AgentOrganization
}

type AgentDetailPageData = {
  csrfToken: string
  urls: {
    detail: string
    list: string
    chat: string
    secrets: string
    emailSettings: string
    smsEnable: string | null
    contactRequests: string
    delete: string
    mcpServersManage: string | null
  }
  agent: AgentSummary
  primaryEmail: PrimaryEndpoint | null
  primarySms: PrimaryEndpoint | null
  dailyCredits: DailyCreditsInfo
  dedicatedIps: DedicatedIpInfo
  allowlist: AllowlistState
  mcpServers: McpServersInfo
  peerLinks: PeerLinksInfo
  webhooks: AgentWebhook[]
  features: {
    organizations: boolean
  }
  reassignment: ReassignmentInfo
}

export type AgentDetailScreenProps = {
  initialData: AgentDetailPageData
}

type FormState = {
  name: string
  charter: string
  isActive: boolean
  dailyCreditInput: string
  sliderValue: number
  dedicatedProxyId: string
}

type AllowlistInput = {
  address: string
  channel: string
  allowInbound: boolean
  allowOutbound: boolean
}

type WebhookModalState = {
  mode: 'create' | 'edit'
  webhook: AgentWebhook | null
  name: string
  url: string
}

export function AgentDetailScreen({ initialData }: AgentDetailScreenProps) {
  const sliderEmptyValue = initialData.dailyCredits.sliderEmptyValue ?? initialData.dailyCredits.sliderMin

  const initialFormState = useMemo<FormState>(
    () => ({
      name: initialData.agent.name,
      charter: initialData.agent.charter,
      isActive: initialData.agent.isActive,
      dailyCreditInput:
        typeof initialData.dailyCredits.limit === 'number' && Number.isFinite(initialData.dailyCredits.limit)
          ? String(Math.round(initialData.dailyCredits.limit))
          : '',
      sliderValue: initialData.dailyCredits.sliderValue ?? sliderEmptyValue,
      dedicatedProxyId: initialData.dedicatedIps.selectedId ?? '',
    }),
    [
      initialData.agent.name,
      initialData.agent.charter,
      initialData.agent.isActive,
      initialData.dailyCredits.limit,
      initialData.dailyCredits.sliderValue,
      initialData.dedicatedIps.selectedId,
      sliderEmptyValue,
    ],
  )

  const [formState, setFormState] = useState<FormState>(initialFormState)
  const [allowlistState, setAllowlistState] = useState(initialData.allowlist)
  const [allowlistError, setAllowlistError] = useState<string | null>(null)
  const [allowlistBusy, setAllowlistBusy] = useState(false)
  const [webhookModal, setWebhookModal] = useState<WebhookModalState | null>(null)
  const [selectedOrgId, setSelectedOrgId] = useState(initialData.reassignment.assignedOrg?.id ?? '')
  const [reassignError, setReassignError] = useState<string | null>(null)
  const [reassigning, setReassigning] = useState(false)

  const hasChanges = useMemo(() => {
    return (
      formState.name !== initialFormState.name ||
      formState.charter !== initialFormState.charter ||
      formState.isActive !== initialFormState.isActive ||
      formState.dailyCreditInput !== initialFormState.dailyCreditInput ||
      formState.sliderValue !== initialFormState.sliderValue ||
      formState.dedicatedProxyId !== initialFormState.dedicatedProxyId
    )
  }, [formState, initialFormState])

  const clampSlider = useCallback(
    (value: number) => {
      return Math.min(Math.max(Number.isFinite(value) ? value : sliderEmptyValue, initialData.dailyCredits.sliderMin), initialData.dailyCredits.sliderMax)
    },
    [initialData.dailyCredits.sliderMax, initialData.dailyCredits.sliderMin, sliderEmptyValue],
  )

  const updateSliderValue = useCallback(
    (value: number) => {
      const normalized = clampSlider(value)
      setFormState((prev) => ({
        ...prev,
        sliderValue: normalized,
        dailyCreditInput: normalized === sliderEmptyValue ? '' : String(Math.round(normalized)),
      }))
    },
    [clampSlider, sliderEmptyValue],
  )

  const handleDailyCreditInputChange = useCallback(
    (value: string) => {
      setFormState((prev) => ({ ...prev, dailyCreditInput: value }))
      if (!value.trim()) {
        updateSliderValue(sliderEmptyValue)
        return
      }
      const numeric = Number(value)
      if (!Number.isFinite(numeric)) {
        updateSliderValue(sliderEmptyValue)
        return
      }
      updateSliderValue(Math.round(numeric))
    },
    [sliderEmptyValue, updateSliderValue],
  )

  const resetForm = useCallback(() => {
    setFormState(initialFormState)
  }, [initialFormState])

  const formatNumber = useCallback((value: number | null, fractionDigits = 0) => {
    if (value === null || !Number.isFinite(value)) {
      return null
    }
    return value.toLocaleString(undefined, {
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    })
  }, [])

  const applyAllowlistPatch = useCallback((payload?: Partial<AllowlistState>) => {
    if (!payload) {
      return
    }
    setAllowlistState((prev) => ({
      ...prev,
      entries: payload.entries ?? prev.entries,
      pendingInvites: payload.pendingInvites ?? prev.pendingInvites,
      ownerEmail: payload.ownerEmail ?? prev.ownerEmail,
      ownerPhone: payload.ownerPhone ?? prev.ownerPhone,
      activeCount: typeof payload.activeCount === 'number' ? payload.activeCount : prev.activeCount,
    }))
  }, [])

  const postAllowlistAction = useCallback(
    async (body: Record<string, string | Blob>) => {
      setAllowlistBusy(true)
      setAllowlistError(null)
      try {
        const formData = new FormData()
        formData.append('csrfmiddlewaretoken', initialData.csrfToken)
        for (const [key, value] of Object.entries(body)) {
          formData.append(key, value)
        }
        const response = await fetch(initialData.urls.detail, {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: formData,
        })
        const data = await response.json()
        if (data.allowlist) {
          applyAllowlistPatch(data.allowlist as Partial<AllowlistState>)
        }
        if (!response.ok || !data.success) {
          throw new Error(data.error || 'Request failed. Please try again.')
        }
      } catch (error) {
        setAllowlistError(error instanceof Error ? error.message : 'Request failed. Please try again.')
        throw error
      } finally {
        setAllowlistBusy(false)
      }
    },
    [applyAllowlistPatch, initialData.csrfToken, initialData.urls.detail],
  )

  const handleAllowlistAdd = useCallback(
    async (input: AllowlistInput) => {
      await postAllowlistAction({
        action: 'add_allowlist',
        channel: input.channel,
        address: input.address,
        allow_inbound: String(input.allowInbound),
        allow_outbound: String(input.allowOutbound),
      })
    },
    [postAllowlistAction],
  )

  const handleAllowlistRemove = useCallback(
    async (entryId: string) => {
      await postAllowlistAction({
        action: 'remove_allowlist',
        entry_id: entryId,
      })
    },
    [postAllowlistAction],
  )

  const handleCancelInvite = useCallback(
    async (inviteId: string) => {
      await postAllowlistAction({
        action: 'cancel_invite',
        invite_id: inviteId,
      })
    },
    [postAllowlistAction],
  )

  const handleReassign = useCallback(
    async (targetOrgId: string | null) => {
      setReassigning(true)
      setReassignError(null)
      try {
        const formData = new FormData()
        formData.append('csrfmiddlewaretoken', initialData.csrfToken)
        formData.append('action', 'reassign_org')
        if (targetOrgId) {
          formData.append('target_org_id', targetOrgId)
        }
        const response = await fetch(initialData.urls.detail, {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: formData,
        })
        const data = await response.json()
        if (!response.ok || !data.success) {
          throw new Error(data.error || 'Reassignment failed. Please try again.')
        }
        if (data.redirect) {
          window.location.href = data.redirect as string
          return
        }
        window.location.reload()
      } catch (error) {
        setReassignError(error instanceof Error ? error.message : 'An unexpected error occurred.')
      } finally {
        setReassigning(false)
      }
    },
    [initialData.csrfToken, initialData.urls.detail],
  )

  const openWebhookModal = useCallback(
    (mode: 'create' | 'edit', webhook: AgentWebhook | null = null) => {
      setWebhookModal({
        mode,
        webhook,
        name: webhook?.name ?? '',
        url: webhook?.url ?? '',
      })
    },
    [],
  )

  const closeWebhookModal = useCallback(() => {
    setWebhookModal(null)
  }, [])

  return (
    <div className="space-y-6 pb-6">
      <header className="bg-white/80 backdrop-blur-sm shadow-xl rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200/70 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-800" id="agent-name-heading">
              {(formState.name || 'Agent').trim()} Settings
            </h1>
            <p className="text-sm text-gray-500 mt-1">Manage your agent settings and preferences</p>
            <a
              href={initialData.urls.list}
              className="group inline-flex items-center gap-2 text-sm text-blue-600 hover:text-blue-800 transition-colors mt-3"
            >
              <ArrowLeft className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform" aria-hidden="true" />
              Back to Agents
            </a>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <a
              href={initialData.urls.chat}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm hover:bg-gray-50 transition-colors"
            >
              <MessageSquare className="w-4 h-4" aria-hidden="true" />
              Web Chat
            </a>
            <a
              href={initialData.urls.secrets}
              className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm hover:bg-gray-50 transition-colors"
            >
              <KeyRound className="w-4 h-4" aria-hidden="true" />
              Secrets
            </a>
            <a
              href={initialData.urls.emailSettings}
              className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm hover:bg-gray-50 transition-colors"
            >
              <Mail className="w-4 h-4" aria-hidden="true" />
              Email Settings
            </a>
          </div>
        </div>
      </header>

      {initialData.agent.pendingTransfer && (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded-xl shadow-md px-5 py-4 flex flex-col gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Info className="w-4 h-4" aria-hidden="true" />
            Transfer pending
          </div>
          <p className="text-sm leading-5">
            This agent is awaiting acceptance from <strong>{initialData.agent.pendingTransfer.toEmail}</strong> (sent {initialData.agent.pendingTransfer.createdAtDisplay}).
            You can continue editing settings, but keep in mind the new owner will take control once they accept.
          </p>
        </div>
      )}

      <form method="post" action={initialData.urls.detail} id="general-settings-form">
        <input type="hidden" name="csrfmiddlewaretoken" value={initialData.csrfToken} />
        {initialData.allowlist.show && (
          <input type="hidden" name="whitelist_policy" value={initialData.agent.whitelistPolicy} />
        )}
        <details className="gobii-card-base group" id="agent-identity" open>
          <summary className="flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none">
            <div>
              <h2 className="text-lg font-semibold text-gray-800">General Settings</h2>
              <p className="text-sm text-gray-500">Core configuration and runtime controls.</p>
            </div>
            <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
          </summary>
          <div className="p-6 sm:p-8">
            <div className="grid sm:grid-cols-12 gap-4 sm:gap-6">
              <div className="sm:col-span-3">
                <label htmlFor="agent-name" className="inline-block text-sm font-medium text-gray-800 mt-2.5">
                  Agent Name
                </label>
                <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
              </div>
              <div className="sm:col-span-9">
                <input
                  id="agent-name"
                  name="name"
                  type="text"
                  value={formState.name}
                  onChange={(event) => setFormState((prev) => ({ ...prev, name: event.target.value }))}
                  className="py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
                />
                <p className="mt-2 text-xs text-gray-500">Choose a memorable name that describes this agent's purpose.</p>
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Status</span>
              </div>
              <div className="sm:col-span-9">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between lg:gap-6 p-4 border border-gray-200 rounded-lg bg-gray-50/60">
                  <div className="flex items-center gap-3">
                    <div className={`flex items-center justify-center w-10 h-10 rounded-full ${formState.isActive ? 'bg-green-100' : 'bg-gray-100'}`}>
                      {formState.isActive ? (
                        <CheckCircle2 className="w-5 h-5 text-green-600" aria-hidden="true" />
                      ) : (
                        <XCircle className="w-5 h-5 text-gray-500" aria-hidden="true" />
                      )}
                    </div>
                    <div>
                      <p className="text-sm font-medium text-gray-800">{formState.isActive ? 'Active' : 'Inactive'}</p>
                      <p className="text-xs text-gray-500">
                        {formState.isActive
                          ? 'This agent is currently running and accepting tasks.'
                          : 'This agent is paused and not accepting tasks.'}
                      </p>
                    </div>
                  </div>
                  <label className="relative inline-flex shrink-0 items-center cursor-pointer">
                    <input
                      type="checkbox"
                      name="is_active"
                      checked={formState.isActive}
                      onChange={(event) => setFormState((prev) => ({ ...prev, isActive: event.target.checked }))}
                      className="sr-only"
                    />
                    <span className="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-blue-600 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:h-5 after:w-5 after:bg-white after:border-gray-300 after:border after:rounded-full after:transition-all" />
                  </label>
                </div>
                <p className="mt-2 text-xs text-gray-500">Toggle the switch and click "Save Changes" to activate or pause the agent.</p>
              </div>

              <div className="sm:col-span-3">
                <label htmlFor="agent-charter" className="inline-block text-sm font-medium text-gray-800 mt-2.5">
                  Assignment
                </label>
                <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
              </div>
              <div className="sm:col-span-9">
                <textarea
                  id="agent-charter"
                  name="charter"
                  rows={4}
                  value={formState.charter}
                  onChange={(event) => setFormState((prev) => ({ ...prev, charter: event.target.value }))}
                  className="py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
                  placeholder="Describe what you want your agent to do..."
                />
                <p className="mt-2 text-xs text-gray-500">Share goals, responsibilities, and key guardrails for this agent.</p>
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Daily Task Credits</span>
                <CircleHelp className="ms-1 inline-block size-3 text-gray-400" aria-hidden="true" />
              </div>
              <div className="sm:col-span-9 space-y-4">
                <DailyCreditSummary dailyCredits={initialData.dailyCredits} formatNumber={formatNumber} />
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-3">
                    <label htmlFor="daily-credit-limit-slider" className="inline-block text-sm font-medium text-gray-700">
                      Soft target (credits/day)
                    </label>
                    <input
                      id="daily-credit-limit-slider"
                      name="daily_credit_limit_slider"
                      type="range"
                      min={initialData.dailyCredits.sliderMin}
                      max={initialData.dailyCredits.sliderMax}
                      step={initialData.dailyCredits.sliderStep}
                      value={formState.sliderValue}
                      onChange={(event) => updateSliderValue(Number(event.target.value))}
                      className="mt-2 w-full"
                      aria-label="Soft target slider"
                    />
                    <div className="mt-1 flex items-center justify-between text-xs text-gray-500" aria-hidden="true">
                      <span>Unlimited</span>
                      <span>{Math.round(initialData.dailyCredits.sliderMax).toLocaleString()} credits/day</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        id="daily-credit-limit-input"
                        name="daily_credit_limit"
                        type="number"
                        step="1"
                        min={initialData.dailyCredits.sliderMin}
                        max={initialData.dailyCredits.sliderMax}
                        value={formState.dailyCreditInput}
                        onChange={(event) => handleDailyCreditInputChange(event.target.value)}
                        className="py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
                        placeholder="Unlimited"
                      />
                      <span className="text-sm text-gray-500">credits/day</span>
                    </div>
                    <p className="mt-1 text-xs text-gray-500">Soft target controls pacing for this agent. Leave the number blank for unlimited.</p>
                  </div>
                </div>
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Dedicated IPs</span>
              </div>
              <div className="sm:col-span-9">
                <DedicatedIpSummary
                  dedicatedIps={initialData.dedicatedIps}
                  organizationName={initialData.agent.organization?.name ?? null}
                  selectedValue={formState.dedicatedProxyId}
                  onChange={(value) => setFormState((prev) => ({ ...prev, dedicatedProxyId: value }))}
                />
              </div>

              <div className="sm:col-span-3">
                <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Created</span>
              </div>
              <div className="sm:col-span-9">
                <div className="py-2 px-3 text-sm text-gray-600">{initialData.agent.createdAtDisplay}</div>
              </div>
            </div>
          </div>
        </details>
      </form>

      <SaveBar
        visible={hasChanges}
        onCancel={resetForm}
      />

      <details className="gobii-card-base group" id="agent-contact-controls">
        <summary className="flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none">
          <div>
            <h2 className="text-lg font-semibold text-gray-800">Contacts &amp; Access</h2>
            <p className="text-sm text-gray-500">Contact endpoints and allowlist management.</p>
          </div>
          <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
        </summary>
        <div className="p-6 sm:p-8 space-y-6">
          <PrimaryContacts
            primaryEmail={initialData.primaryEmail}
            primarySms={initialData.primarySms}
            emailSettingsUrl={initialData.urls.emailSettings}
            smsEnableUrl={initialData.urls.smsEnable}
          />

          {initialData.allowlist.show && (
            <AllowlistManager
              state={allowlistState}
              error={allowlistError}
              busy={allowlistBusy}
              onAdd={handleAllowlistAdd}
              onRemove={handleAllowlistRemove}
              onCancelInvite={handleCancelInvite}
              contactRequestsUrl={initialData.urls.contactRequests}
            />
          )}
        </div>
      </details>

      <IntegrationsSection
        csrfToken={initialData.csrfToken}
        urls={initialData.urls}
        mcpServers={initialData.mcpServers}
        peerLinks={initialData.peerLinks}
      />

      <WebhooksSection webhooks={initialData.webhooks} csrfToken={initialData.csrfToken} detailUrl={initialData.urls.detail} onEdit={openWebhookModal} />

      <ActionsSection
        csrfToken={initialData.csrfToken}
        urls={initialData.urls}
        agent={initialData.agent}
        features={initialData.features}
        reassignment={initialData.reassignment}
        selectedOrgId={selectedOrgId}
        onOrgChange={setSelectedOrgId}
        onReassign={handleReassign}
        reassignError={reassignError}
        reassigning={reassigning}
      />

      {webhookModal && (
        <WebhookModal
          csrfToken={initialData.csrfToken}
          detailUrl={initialData.urls.detail}
          state={webhookModal}
          onClose={closeWebhookModal}
          onChange={(next) => setWebhookModal((prev) => (prev ? { ...prev, ...next } : prev))}
        />
      )}
    </div>
  )
}

type DailyCreditSummaryProps = {
  dailyCredits: DailyCreditsInfo
  formatNumber: (value: number | null, fractionDigits?: number) => string | null
}

function DailyCreditSummary({ dailyCredits, formatNumber }: DailyCreditSummaryProps) {
  const usageDisplay = formatNumber(dailyCredits.usage, 2)
  const limitDisplay = dailyCredits.limit === null ? 'Unlimited' : formatNumber(dailyCredits.limit, 0)
  const softRemaining = formatNumber(dailyCredits.softRemaining, 2)
  const hardRemaining = formatNumber(dailyCredits.remaining, 2)

  return (
    <div className="p-4 border border-gray-200 rounded-lg bg-white/70 space-y-4">
      {dailyCredits.unlimited ? (
        <div>
          <p className="text-sm text-gray-700">Soft target is currently Unlimited, so this agent will keep running until your overall credits run out.</p>
          {dailyCredits.nextResetLabel && <p className="text-xs text-gray-500 mt-1">Daily usage still resets at {dailyCredits.nextResetLabel}.</p>}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm text-gray-700">
            <span>Soft target progress</span>
            <span className="font-medium">
              {usageDisplay} / {limitDisplay} credits
            </span>
          </div>
          <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-500 rounded-full transition-all"
              style={{ width: `${Math.min(dailyCredits.softPercentUsed ?? 0, 100)}%` }}
            />
          </div>
          {softRemaining && <p className="text-xs text-gray-500">Remaining before soft target: {softRemaining} credits.</p>}
          {dailyCredits.nextResetLabel && <p className="text-xs text-gray-500">Daily usage resets at {dailyCredits.nextResetLabel}.</p>}
        </div>
      )}
      {hardRemaining && <p className="text-xs text-gray-500">Hard limit remaining: {hardRemaining} credits.</p>}
    </div>
  )
}

type DedicatedIpSummaryProps = {
  dedicatedIps: DedicatedIpInfo
  organizationName: string | null
  selectedValue: string
  onChange: (value: string) => void
}

function DedicatedIpSummary({ dedicatedIps, organizationName, selectedValue, onChange }: DedicatedIpSummaryProps) {
  return (
    <div className="text-sm text-gray-600 space-y-4" data-dedicated-ip-total={dedicatedIps.total}>
      <p className="text-sm text-gray-500">Monitor and assign dedicated IP addresses reserved for this account.</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="border border-gray-200 rounded-lg bg-gray-50 p-4">
          <p className="text-xs uppercase tracking-wide text-gray-500">Total Reserved</p>
          <p className="text-2xl font-semibold text-gray-800 mt-1">{dedicatedIps.total}</p>
          <p className="text-xs text-gray-500 mt-3">
            {dedicatedIps.ownerType === 'organization' && organizationName
              ? `Dedicated IPs owned by ${organizationName}.`
              : 'Dedicated IPs owned by your account.'}
          </p>
          {!dedicatedIps.multiAssign && <p className="text-xs text-amber-600 mt-1">Each dedicated IP can be assigned to only one agent at a time.</p>}
          {dedicatedIps.total === 0 && <p className="text-xs text-gray-500 mt-1">Purchase dedicated IPs in Billing to make them available here.</p>}
        </div>
        <div className="border border-gray-200 rounded-lg bg-gray-50 p-4">
          <p className="text-xs uppercase tracking-wide text-gray-500">Available to Assign</p>
          <p className="text-2xl font-semibold text-gray-800 mt-1">{dedicatedIps.available}</p>
          {dedicatedIps.options.length > 0 ? (
            <div className="mt-4 space-y-2">
              <label htmlFor="dedicated-proxy-id" className="inline-block text-sm font-medium text-gray-800">
                Assigned Dedicated IP
              </label>
              <select
                id="dedicated-proxy-id"
                name="dedicated_proxy_id"
                value={selectedValue}
                onChange={(event) => onChange(event.target.value)}
                className="mt-1 py-2 px-3 block w-full border-gray-200 shadow-sm rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
              >
                <option value="">Use shared proxy pool</option>
                {dedicatedIps.options.map((option) => (
                  <option key={option.id} value={option.id} disabled={option.disabled}>
                    {option.label}
                    {option.inUseElsewhere ? ' (In use)' : ''}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-gray-500">
                Selecting a dedicated IP locks this agent to that address. Leave it on "Use shared proxy pool" to continue using shared proxies.
              </p>
              {!dedicatedIps.multiAssign && <p className="mt-1 text-xs text-amber-600">IPs already assigned to other agents are disabled.</p>}
            </div>
          ) : (
            <p className="text-xs text-gray-500 mt-4">No dedicated IPs are currently available to assign.</p>
          )}
        </div>
      </div>
    </div>
  )
}

type SaveBarProps = {
  visible: boolean
  onCancel: () => void
}

function SaveBar({ visible, onCancel }: SaveBarProps) {
  if (!visible) {
    return null
  }
  return (
    <div id="agent-save-bar" className="fixed inset-x-0 bottom-0 z-40 pointer-events-none">
      <div className="pointer-events-auto mx-auto w-full max-w-5xl px-4 pb-4">
        <div className="flex flex-col gap-3 rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-[0_8px_30px_rgba(15,23,42,0.25)] sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2 text-sm text-gray-700">
            <Info className="h-4 w-4 text-blue-600" aria-hidden="true" />
            <span>You have unsaved changes</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              form="general-settings-form"
              className="inline-flex items-center gap-2 rounded-lg border border-transparent bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
            >
              <Check className="h-4 w-4" aria-hidden="true" />
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

type PrimaryContactsProps = {
  primaryEmail: PrimaryEndpoint | null
  primarySms: PrimaryEndpoint | null
  emailSettingsUrl: string
  smsEnableUrl: string | null
}

function PrimaryContacts({ primaryEmail, primarySms, emailSettingsUrl, smsEnableUrl }: PrimaryContactsProps) {
  return (
    <div className="grid sm:grid-cols-12 gap-4 sm:gap-6">
      <div className="sm:col-span-3">
        <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Primary Email</span>
      </div>
      <div className="sm:col-span-9">
        {primaryEmail ? (
          <>
            <input
              id="agent-email"
              type="text"
              value={primaryEmail.address}
              readOnly
              className="py-2 px-3 block w-full border-gray-200 bg-gray-100 shadow-sm rounded-lg text-sm"
            />
            <p className="mt-2 text-xs text-gray-500">The agent's primary email address for communication.</p>
            <div className="mt-2 space-y-1">
              <a href={emailSettingsUrl} className="text-sm text-blue-600 hover:text-blue-800">
                Manage Email Settings
              </a>
              {!primarySms && smsEnableUrl && (
                <div>
                  <a href={smsEnableUrl} className="text-sm text-blue-600 hover:text-blue-800">
                    Enable SMS
                  </a>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="py-2 px-3 text-sm text-gray-600 bg-gray-50 border border-dashed border-gray-300 rounded">
            Not configured.{' '}
            <a href={emailSettingsUrl} className="text-blue-600 hover:text-blue-800">
              Set up email
            </a>
          </div>
        )}
      </div>

      {primarySms && (
        <>
          <div className="sm:col-span-3">
            <span className="inline-block text-sm font-medium text-gray-800 mt-2.5">Primary SMS</span>
          </div>
          <div className="sm:col-span-9">
            <input
              id="agent-sms"
              type="text"
              value={primarySms.address}
              readOnly
              className="py-2 px-3 block w-full border-gray-200 bg-gray-100 shadow-sm rounded-lg text-sm"
            />
            <p className="mt-2 text-xs text-gray-500">The agent's primary SMS address for communication. This cannot be changed.</p>
          </div>
        </>
      )}
    </div>
  )
}

type AllowlistManagerProps = {
  state: AllowlistState
  error: string | null
  busy: boolean
  onAdd: (input: AllowlistInput) => Promise<void>
  onRemove: (entryId: string) => Promise<void>
  onCancelInvite: (inviteId: string) => Promise<void>
  contactRequestsUrl: string
}

function AllowlistManager({ state, error, busy, onAdd, onRemove, onCancelInvite, contactRequestsUrl }: AllowlistManagerProps) {
  const [channel, setChannel] = useState('email')
  const [address, setAddress] = useState('')
  const [allowInbound, setAllowInbound] = useState(true)
  const [allowOutbound, setAllowOutbound] = useState(true)

  const handleSubmit = async () => {
    if (!address.trim()) {
      return
    }
    try {
      await onAdd({ channel, address: address.trim(), allowInbound, allowOutbound })
      setAddress('')
      setAllowInbound(true)
      setAllowOutbound(true)
    } catch (error) {
      // Errors are surfaced via allowlistError state
      console.error(error)
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500">
        By default, the agent owner and organization members can communicate with this agent. You can add additional contacts below. Note: Multi-recipient messaging is limited to email only.
      </p>

      <div className="space-y-4">
        <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg space-y-2">
          <h4 className="text-sm font-medium text-gray-700">Add Allowed Contact</h4>
          <div className="flex gap-2">
            <select
              id="allowlist-channel"
              name="channel"
              value={channel}
              onChange={(event) => setChannel(event.target.value)}
              className="py-1.5 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
            >
              <option value="email">Email</option>
            </select>
            <input
              type="email"
              id="allowlist-address"
              name="address"
              placeholder="email@example.com"
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              className="flex-1 py-1.5 px-2 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
            />
          </div>
          <div className="flex gap-4 items-center">
            <label className="flex items-center gap-1 text-sm">
              <input
                type="checkbox"
                checked={allowInbound}
                onChange={(event) => setAllowInbound(event.target.checked)}
                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span>Allow Inbound</span>
              <span className="text-xs text-gray-500">(can send to agent)</span>
            </label>
            <label className="flex items-center gap-1 text-sm">
              <input
                type="checkbox"
                checked={allowOutbound}
                onChange={(event) => setAllowOutbound(event.target.checked)}
                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span>Allow Outbound</span>
              <span className="text-xs text-gray-500">(agent can send to)</span>
            </label>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleSubmit}
              disabled={busy || !address.trim()}
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              Add
            </button>
            <button
              type="button"
              onClick={() => {
                setAddress('')
                setAllowInbound(true)
                setAllowOutbound(true)
              }}
              className="px-3 py-1.5 text-sm bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400"
            >
              Cancel
            </button>
          </div>
          {error && <div className="text-xs text-red-600">{error}</div>}
        </div>

        {state.pendingContactRequests > 0 && (
          <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-yellow-600" aria-hidden="true" />
                <span className="text-sm font-medium text-yellow-800">
                  {state.pendingContactRequests} Contact Request{state.pendingContactRequests === 1 ? '' : 's'} Pending
                </span>
              </div>
              <a href={contactRequestsUrl} className="text-sm font-medium text-yellow-700 hover:text-yellow-900 underline">
                Review
              </a>
            </div>
          </div>
        )}

        <div className="p-3 bg-gray-50 rounded-lg">
          <div className="flex justify-between items-center mb-2">
            <h4 className="text-sm font-medium text-gray-700">Allowed Contacts</h4>
            <span className="text-xs text-gray-500">
              {state.activeCount} / {state.maxContacts ?? 'Unlimited'} contacts
            </span>
          </div>
          <AllowlistEntries state={state} onRemove={onRemove} onCancelInvite={onCancelInvite} />
        </div>
      </div>
    </div>
  )
}

type AllowlistEntriesProps = {
  state: AllowlistState
  onRemove: (entryId: string) => Promise<void>
  onCancelInvite: (inviteId: string) => Promise<void>
}

function AllowlistEntries({ state, onRemove, onCancelInvite }: AllowlistEntriesProps) {
  const hasContacts = state.entries.length > 0 || state.pendingInvites.length > 0
  const renderChannelIcon = (channel: string, className = 'w-4 h-4 text-gray-400') =>
    channel?.toLowerCase() === 'sms' ? (
      <Phone className={className} aria-hidden="true" />
    ) : (
      <Mail className={className} aria-hidden="true" />
    )

  return (
    <div className="space-y-2">
      {(state.ownerEmail || state.ownerPhone) && (
        <div className="text-xs text-gray-500 mb-2">
          <div className="font-medium">Owner (always allowed in Default mode):</div>
          {state.ownerEmail && (
            <div className="flex items-center justify-between py-1 px-2">
              <span className="flex items-center gap-2">
                <Mail className="w-3 h-3 text-gray-400" aria-hidden="true" />
                {state.ownerEmail}
              </span>
            </div>
          )}
          {state.ownerPhone && (
            <div className="flex items-center justify-between py-1 px-2">
              <span className="flex items-center gap-2">
                <Phone className="w-3 h-3 text-gray-400" aria-hidden="true" />
                {state.ownerPhone}
              </span>
            </div>
          )}
          <div className="border-t border-gray-200 my-2" />
        </div>
      )}

      {state.pendingInvites.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2 font-medium">Pending Invitations:</div>
          {state.pendingInvites.map((invite) => (
            <div key={invite.id} className="py-2 px-3 bg-yellow-50 rounded-lg border border-yellow-200 mb-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium flex items-center gap-2">
                  {renderChannelIcon(invite.channel, 'w-4 h-4 text-gray-400')}
                  {invite.address}
                </span>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-yellow-700 font-medium">Pending</span>
                  <button
                    type="button"
                    onClick={() => {
                      if (!confirm('Cancel this invitation?')) {
                        return
                      }
                      void onCancelInvite(invite.id)
                    }}
                    className="text-red-600 hover:text-red-800 text-xs font-medium"
                  >
                    Cancel
                  </button>
                </div>
              </div>
              <AllowlistDirectionFlags allowInbound={invite.allowInbound} allowOutbound={invite.allowOutbound} labelColor="text-yellow-700" />
            </div>
          ))}
          <div className="border-t border-gray-200 my-2" />
        </div>
      )}

      {state.entries.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2 font-medium">Allowed Contacts:</div>
          {state.entries.map((entry) => (
            <div key={entry.id} className="flex items-center justify-between py-2 px-3 bg-gray-50 rounded-lg group hover:bg-gray-100 border border-gray-200 mb-2">
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium flex items-center gap-2">
                    {renderChannelIcon(entry.channel, 'w-4 h-4 text-gray-400')}
                    {entry.address}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      if (!confirm('Remove this contact from the allowlist?')) {
                        return
                      }
                      void onRemove(entry.id)
                    }}
                    className="text-xs text-red-600 hover:text-red-800 opacity-0 group-hover:opacity-100 transition-opacity ml-2"
                  >
                    Remove
                  </button>
                </div>
                <AllowlistDirectionFlags allowInbound={entry.allowInbound} allowOutbound={entry.allowOutbound} />
              </div>
            </div>
          ))}
        </div>
      )}

      {!hasContacts && <div className="text-sm text-gray-500 py-2">No additional contacts configured yet.</div>}
    </div>
  )
}

type AllowlistDirectionFlagsProps = {
  allowInbound: boolean
  allowOutbound: boolean
  labelColor?: string
}

function AllowlistDirectionFlags({ allowInbound, allowOutbound, labelColor }: AllowlistDirectionFlagsProps) {
  const inboundClass = allowInbound ? 'text-green-700' : 'text-gray-400 line-through'
  const outboundClass = allowOutbound ? 'text-blue-700' : 'text-gray-400 line-through'
  const colorClass = labelColor ?? 'text-gray-500'

  return (
    <div className="flex gap-3 mt-1 ml-6">
      <div className={`flex items-center gap-1 text-xs ${colorClass}`}>
        <ArrowDownToLine className={`w-4 h-4 ${allowInbound ? 'text-green-600' : 'text-gray-400'}`} aria-hidden="true" />
        <span className={`text-xs ${inboundClass}`}>Receives from contact</span>
      </div>
      <div className={`flex items-center gap-1 text-xs ${colorClass}`}>
        <ArrowUpFromLine className={`w-4 h-4 ${allowOutbound ? 'text-blue-600' : 'text-gray-400'}`} aria-hidden="true" />
        <span className={`text-xs ${outboundClass}`}>Sends to contact</span>
      </div>
    </div>
  )
}

type IntegrationsSectionProps = {
  csrfToken: string
  urls: AgentDetailPageData['urls']
  mcpServers: McpServersInfo
  peerLinks: PeerLinksInfo
}

function IntegrationsSection({ csrfToken, mcpServers, peerLinks }: IntegrationsSectionProps) {
  return (
    <details className="gobii-card-base group" id="agent-integrations">
      <summary className="flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Integrations</h2>
          <p className="text-sm text-gray-500">MCP servers, peer links, and webhooks.</p>
        </div>
        <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
      </summary>
      <div className="divide-y divide-gray-200/70">
        <section className="p-6 sm:p-8 space-y-6">
          <div>
            <h3 className="text-base font-semibold text-gray-800">MCP Servers</h3>
            <p className="text-sm text-gray-500">Platform and organization MCP servers are always enabled for this agent. Configure optional personal servers below.</p>
          </div>

          {mcpServers.inherited.length > 0 && (
            <div className="space-y-3">
              <h4 className="text-sm font-semibold text-gray-700">Inherited Servers</h4>
              <ul className="space-y-2">
                {mcpServers.inherited.map((server) => (
                  <li key={server.id} className="flex items-start justify-between gap-3 border border-gray-200 bg-gray-50 rounded-lg px-4 py-3">
                    <div>
                      <p className="text-sm font-medium text-gray-800">{server.displayName}</p>
                      {server.description && <p className="text-sm text-gray-600">{server.description}</p>}
                    </div>
                    <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{server.scope}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {mcpServers.personal.length > 0 ? (
            mcpServers.showPersonalForm ? (
              <div className="border border-gray-200 rounded-xl bg-white p-4">
                <form method="post" className="space-y-4">
                  <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
                  <input type="hidden" name="mcp_server_action" value="update_personal" />
                  <div className="grid gap-3 md:grid-cols-2">
                    {mcpServers.personal.map((server) => (
                      <label key={server.id} className="flex items-start gap-3 border border-gray-200 rounded-lg px-3 py-3">
                        <input
                          type="checkbox"
                          className="mt-1 h-4 w-4 text-blue-600 border-gray-300 rounded"
                          name="personal_servers"
                          value={server.id}
                          defaultChecked={server.assigned}
                        />
                        <div>
                          <p className="text-sm font-medium text-gray-800">{server.displayName}</p>
                          {server.description && <p className="text-sm text-gray-600">{server.description}</p>}
                        </div>
                      </label>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    {mcpServers.canManage && mcpServers.manageUrl && (
                      <a
                        href={mcpServers.manageUrl}
                        className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-800 shadow-sm transition-colors hover:bg-gray-50"
                      >
                        <ServerCog className="h-4 w-4" aria-hidden="true" />
                        Manage All Servers
                      </a>
                    )}
                    <button type="submit" className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700">
                      Save Personal Servers
                    </button>
                  </div>
                </form>
              </div>
            ) : (
              <p className="text-sm text-gray-500">Personal MCP servers are managed on personal agents. Switch to a personal agent to configure access.</p>
            )
          ) : (
            mcpServers.inherited.length === 0 && <p className="text-sm text-gray-500">No MCP servers are available for this agent yet.</p>
          )}
        </section>

        <section className="p-6 sm:p-8 space-y-6">
          <div>
            <h3 className="text-base font-semibold text-gray-800">Agent Contacts (Peer Links)</h3>
            <p className="text-sm text-gray-500">Create direct channels between this agent and other agents you control.</p>
          </div>

          <div className="bg-gray-50 border border-gray-200 rounded-xl p-4">
            <form method="post" className="space-y-3">
              <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
              <input type="hidden" name="peer_link_action" value="create" />
              <div className="grid md:grid-cols-4 gap-3">
                <div className="md:col-span-2">
                  <label className="block text-xs font-medium text-gray-600 mb-1">Agent</label>
                  <select name="peer_agent_id" className="w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500">
                    <option value="">Select an agent...</option>
                    {peerLinks.candidates.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.name}
                      </option>
                    ))}
                  </select>
                  {peerLinks.candidates.length === 0 && (
                    <p className="mt-2 text-xs text-gray-500">No additional eligible agents available.</p>
                  )}
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Messages / Window</label>
                  <input
                    type="number"
                    min="1"
                    name="messages_per_window"
                    defaultValue={peerLinks.defaults.messagesPerWindow}
                    className="w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Window Hours</label>
                  <input
                    type="number"
                    min="1"
                    name="window_hours"
                    defaultValue={peerLinks.defaults.windowHours}
                    className="w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
                  />
                </div>
              </div>
              <button
                type="submit"
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700"
                disabled={peerLinks.candidates.length === 0}
              >
                <Plus className="w-4 h-4" aria-hidden="true" />
                Create Link
              </button>
            </form>
          </div>

          {peerLinks.entries.length > 0 ? (
            <div className="overflow-hidden border border-gray-200 rounded-xl">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Agent</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Quota</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Remaining</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Next Reset</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Feature Flag</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {peerLinks.entries.map((entry) => (
                    <tr key={entry.id} className="align-top">
                      <td className="px-4 py-3 text-sm text-gray-800">
                        <div className="font-medium">{entry.counterpartName ?? '(Agent unavailable)'}</div>
                        <div className="text-xs text-gray-500 mt-1">Linked {entry.createdOnLabel}</div>
                        <div className="text-xs mt-1">
                          Status:{' '}
                          <span className={entry.isEnabled ? 'text-green-600' : 'text-gray-500'}>
                            {entry.isEnabled ? 'Enabled' : 'Disabled'}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700">
                        {entry.messagesPerWindow} / {entry.windowHours} h
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700">{entry.state?.creditsRemaining ?? '--'}</td>
                      <td className="px-4 py-3 text-sm text-gray-700">{entry.state?.windowResetLabel ?? '--'}</td>
                      <td className="px-4 py-3 text-sm text-gray-700">{entry.featureFlag ?? '--'}</td>
                      <td className="px-4 py-3 text-sm text-gray-700 space-y-2">
                        <form method="post" className="space-y-2">
                          <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
                          <input type="hidden" name="peer_link_action" value="update" />
                          <input type="hidden" name="link_id" value={entry.id} />
                          <div className="flex flex-col sm:flex-row sm:items-center sm:gap-2">
                            <input
                              type="number"
                              min="1"
                              name="messages_per_window"
                              defaultValue={entry.messagesPerWindow}
                              className="w-full sm:w-24 py-1.5 px-2 text-xs border-gray-300 rounded-md focus:border-blue-500 focus:ring-blue-500"
                            />
                            <input
                              type="number"
                              min="1"
                              name="window_hours"
                              defaultValue={entry.windowHours}
                              className="w-full sm:w-20 py-1.5 px-2 text-xs border-gray-300 rounded-md focus:border-blue-500 focus:ring-blue-500"
                            />
                            <input
                              type="text"
                              name="feature_flag"
                              defaultValue={entry.featureFlag ?? ''}
                              placeholder="Flag (optional)"
                              className="w-full sm:w-28 py-1.5 px-2 text-xs border-gray-300 rounded-md focus:border-blue-500 focus:ring-blue-500"
                            />
                          </div>
                          <label className="inline-flex items-center gap-2 text-xs text-gray-600">
                            <input
                              type="checkbox"
                              name="is_enabled"
                              defaultChecked={entry.isEnabled}
                              className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                            />
                            <span>Link enabled</span>
                          </label>
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="submit"
                              className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700"
                            >
                              <Check className="w-3.5 h-3.5" aria-hidden="true" />
                              Update
                            </button>
                            <button
                              type="submit"
                              name="peer_link_action"
                              value="delete"
                              className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-red-600 border border-red-200 rounded-md hover:bg-red-50"
                              onClick={(event) => {
                                if (!confirm('Remove this link? This cannot be undone.')) {
                                  event.preventDefault()
                                }
                              }}
                            >
                              <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                              Remove
                            </button>
                          </div>
                        </form>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-4 bg-gray-50 border border-dashed border-gray-300 rounded-xl text-sm text-gray-600">
              No peer links yet. Use the form above to connect this agent with another agent you control.
            </div>
          )}
        </section>
      </div>
    </details>
  )
}

type WebhooksSectionProps = {
  webhooks: AgentWebhook[]
  csrfToken: string
  detailUrl: string
  onEdit: (mode: 'create' | 'edit', webhook?: AgentWebhook | null) => void
}

function WebhooksSection({ webhooks, csrfToken, onEdit }: WebhooksSectionProps) {
  return (
    <details className="gobii-card-base group" id="agent-webhooks">
      <summary className="flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Outbound Webhooks</h2>
          <p className="text-sm text-gray-500">Manage webhook endpoints this agent can trigger.</p>
        </div>
        <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
      </summary>
      <div className="p-6 sm:p-8 space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <p className="text-sm text-gray-500">Webhooks notify your systems when the agent completes important actions.</p>
          <button
            type="button"
            onClick={() => onEdit('create')}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg shadow-sm hover:bg-blue-700"
          >
            <Plus className="w-4 h-4" aria-hidden="true" />
            Add Webhook
          </button>
        </div>

        {webhooks.length > 0 ? (
          <div className="overflow-hidden border border-gray-200 rounded-xl">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Name</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">URL</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Actions</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {webhooks.map((webhook) => (
                  <tr key={webhook.id}>
                    <td className="px-4 py-3 text-sm text-gray-800">{webhook.name}</td>
                    <td className="px-4 py-3 text-sm text-gray-600 break-all">{webhook.url}</td>
                    <td className="px-4 py-3 text-sm text-gray-700 space-y-2">
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => onEdit('edit', webhook)}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-gray-200 text-gray-700 hover:bg-gray-50"
                        >
                          Edit
                        </button>
                        <form method="post" className="inline-flex">
                          <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
                          <input type="hidden" name="webhook_action" value="delete" />
                          <input type="hidden" name="webhook_id" value={webhook.id} />
                          <button
                            type="submit"
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-red-200 text-red-600 hover:bg-red-50"
                            onClick={(event) => {
                              if (!confirm('Remove this webhook? This cannot be undone.')) {
                                event.preventDefault()
                              }
                            }}
                          >
                            <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                            Delete
                          </button>
                        </form>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4 bg-gray-50 border border-dashed border-gray-300 rounded-xl text-sm text-gray-600">
            No webhooks yet. Add one to let your agent notify external systems.
          </div>
        )}
      </div>
    </details>
  )
}

type WebhookModalProps = {
  csrfToken: string
  detailUrl: string
  state: WebhookModalState
  onClose: () => void
  onChange: (state: Partial<WebhookModalState>) => void
}

function WebhookModal({ csrfToken, detailUrl, state, onClose, onChange }: WebhookModalProps) {
  return (
    <div className="fixed inset-0 z-50 overflow-y-auto" role="dialog" aria-modal="true">
      <div className="flex min-h-screen items-center justify-center px-4">
        <div className="fixed inset-0 bg-gray-500/70 backdrop-blur-sm" aria-hidden="true" onClick={onClose} />
        <span className="hidden sm:inline-block sm:h-screen sm:align-middle" aria-hidden="true">
          &#8203;
        </span>
        <div className="inline-block w-full transform overflow-hidden rounded-2xl bg-white text-left align-middle shadow-2xl transition-all sm:my-8 sm:max-w-lg">
          <div className="px-6 py-5 border-b border-gray-200/70 flex items-start justify-between gap-4">
            <div>
              <h3 className="text-lg font-semibold text-gray-900">{state.mode === 'create' ? 'Add Webhook' : 'Edit Webhook'}</h3>
              <p className="mt-1 text-sm text-gray-500">Provide a human-friendly name and the destination URL. The agent will send JSON payloads to this URL.</p>
            </div>
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-full p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100"
              onClick={onClose}
            >
              <span className="sr-only">Close</span>
              <X className="w-5 h-5" aria-hidden="true" />
            </button>
          </div>
          <form method="post" action={detailUrl} className="px-6 py-5 space-y-5">
            <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
            <input type="hidden" name="webhook_action" value={state.mode === 'create' ? 'create' : 'update'} />
            {state.mode === 'edit' && state.webhook && <input type="hidden" name="webhook_id" value={state.webhook.id} />}
            <div>
              <label htmlFor="webhook-name-field" className="block text-sm font-medium text-gray-700">
                Webhook Name
              </label>
              <input
                type="text"
                id="webhook-name-field"
                name="webhook_name"
                required
                value={state.name}
                onChange={(event) => onChange({ name: event.target.value })}
                className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
                placeholder="A descriptive name for this webhook"
              />
            </div>
            <div>
              <label htmlFor="webhook-url-field" className="block text-sm font-medium text-gray-700">
                Destination URL
              </label>
              <input
                type="url"
                id="webhook-url-field"
                name="webhook_url"
                required
                value={state.url}
                onChange={(event) => onChange({ url: event.target.value })}
                className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
                placeholder="https://example.com/webhooks/gobii"
              />
              <p className="mt-2 text-xs text-gray-500">
                We send a POST request with JSON payload including <code className="bg-gray-100 px-1 py-0.5 rounded">agent_id</code>,{' '}
                <code className="bg-gray-100 px-1 py-0.5 rounded">webhook_name</code>, and your provided <code className="bg-gray-100 px-1 py-0.5 rounded">payload</code>.
              </p>
            </div>
            <div className="flex items-center justify-end gap-3 pt-2">
              <button type="button" className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2">
                Save Webhook
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}

type ActionsSectionProps = {
  csrfToken: string
  urls: AgentDetailPageData['urls']
  agent: AgentSummary
  features: AgentDetailPageData['features']
  reassignment: ReassignmentInfo
  selectedOrgId: string
  onOrgChange: (value: string) => void
  onReassign: (targetOrgId: string | null) => Promise<void>
  reassignError: string | null
  reassigning: boolean
}

function ActionsSection({
  csrfToken,
  urls,
  agent,
  features,
  reassignment,
  selectedOrgId,
  onOrgChange,
  onReassign,
  reassignError,
  reassigning,
}: ActionsSectionProps) {
  return (
    <details className="gobii-card-base group" id="agent-ownership">
      <summary className="flex items-center justify-between gap-3 px-6 py-4 border-b border-gray-200/70 cursor-pointer list-none">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Actions</h2>
          <p className="text-sm text-gray-500">Ownership, transfer, and deletion tools.</p>
        </div>
        <ChevronDown className="w-4 h-4 text-gray-500 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
      </summary>
      <div className="divide-y divide-gray-200/70">
        {features.organizations && reassignment.enabled && (
          <section className="p-6 sm:p-8 space-y-4">
            <div>
              <h3 className="text-base font-semibold text-gray-800">Organization Assignment</h3>
              <p className="text-sm text-gray-500">Switch this agent between your personal workspace and an organization you manage.</p>
            </div>
            {agent.organization ? (
              <div className="space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                  <span className="text-sm text-gray-700">
                    Currently assigned to <strong>{agent.organization.name}</strong>
                  </span>
                  <button
                    type="button"
                    onClick={() => onReassign(null)}
                    className="px-3 py-1.5 text-sm bg-gray-600 text-white rounded-lg hover:bg-gray-700 disabled:opacity-50"
                    disabled={reassigning}
                  >
                    Move to Personal
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center sm:gap-3">
                  <select
                    id="target-org-id"
                    value={selectedOrgId}
                    onChange={(event) => onOrgChange(event.target.value)}
                    className="py-2 border-gray-200 rounded-lg text-sm focus:border-blue-500 focus:ring-blue-500"
                  >
                    <option value="">Select organization...</option>
                    {reassignment.organizations.map((org) => (
                      <option key={org.id} value={org.id}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => onReassign(selectedOrgId || null)}
                    className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                    disabled={!selectedOrgId || reassigning}
                  >
                    Assign to Organization
                  </button>
                </div>
                <p className="text-xs text-gray-500">Name must be unique within the selected organization.</p>
              </div>
            )}
            {reassignError && <div className="text-xs text-red-600">{reassignError}</div>}
          </section>
        )}

        <section className="p-6 sm:p-8 space-y-4">
          <div>
            <h3 className="text-base font-semibold text-gray-800">Transfer Ownership</h3>
            <p className="text-sm text-gray-500">Send this agent to someone else. They can accept or decline from their dashboard.</p>
          </div>

          {agent.pendingTransfer ? (
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 bg-indigo-50 border border-indigo-100 rounded-lg p-4">
              <div>
                <p className="text-sm text-indigo-800">
                  Transfer invitation sent to <strong>{agent.pendingTransfer.toEmail}</strong> on {agent.pendingTransfer.createdAtDisplay}.
                </p>
                <p className="text-xs text-indigo-700 mt-1">They'll need to sign in with that email to accept.</p>
              </div>
              <form method="post" className="flex">
                <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
                <input type="hidden" name="action" value="cancel_transfer_invite" />
                <button type="submit" className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50">
                  Cancel Invitation
                </button>
              </form>
            </div>
          ) : (
            <form method="post" className="space-y-4">
              <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken} />
              <input type="hidden" name="action" value="transfer_agent" />
              <div>
                <label htmlFor="transfer-email" className="text-sm font-medium text-gray-700">
                  Recipient email
                </label>
                <input
                  id="transfer-email"
                  name="transfer_email"
                  type="email"
                  required
                  placeholder="user@example.com"
                  className="mt-1 block w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
                />
              </div>
              <div>
                <label htmlFor="transfer-message" className="text-sm font-medium text-gray-700">
                  Message <span className="text-gray-400">(optional)</span>
                </label>
                <textarea
                  id="transfer-message"
                  name="transfer_message"
                  rows={2}
                  className="mt-1 block w-full py-2 px-3 text-sm border-gray-300 rounded-lg focus:border-blue-500 focus:ring-blue-500"
                  placeholder="Share any context you'd like them to know."
                />
              </div>
              <div className="flex justify-end">
                <button type="submit" className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2">
                  Send Transfer Invite
                </button>
              </div>
            </form>
          )}
        </section>

        <section className="p-6 sm:p-8">
          <div className="flex gap-x-4">
            <div className="flex-shrink-0">
              <div className="flex items-center justify-center w-12 h-12 rounded-full bg-red-100 border-4 border-red-50">
                <ShieldAlert className="w-6 h-6 text-red-600" aria-hidden="true" />
              </div>
            </div>
            <div className="flex-grow space-y-4">
              <div>
                <h3 className="text-lg font-bold text-red-800">Danger Zone</h3>
                <p className="text-sm text-red-700">Permanently delete this agent and all of its data. This action cannot be undone and will immediately stop any running tasks.</p>
              </div>
              <button
                {...{
                  'hx-delete': urls.delete,
                  'hx-confirm': 'Are you sure you want to delete this agent? This action cannot be undone and will permanently remove all agent data and stop any running tasks.',
                  'hx-target': 'body',
                  'hx-swap': 'none',
                }}
                className="py-2 px-4 inline-flex items-center gap-x-2 text-sm font-medium rounded-lg border border-red-300 bg-red-50 text-red-700 hover:bg-red-100 hover:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
              >
                <Trash2 className="w-4 h-4" aria-hidden="true" />
                Delete Agent
              </button>
            </div>
          </div>
        </section>
      </div>
    </details>
  )
}
