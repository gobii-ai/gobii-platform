import { useEffect, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { HttpError, jsonRequest } from '../../../../api/http'
import type { ToolDetailProps } from '../../tooling/types'
import { isRecord, parseResultObject } from '../../../../util/objectUtils'
import { ExternalLinkText, KeyValueList, Section, ToolResultCard } from '../shared'
import { extractFirstUrl, isNonEmptyString, stringify } from '../utils'

type ContactDetail = {
  channel: string | null
  address: string | null
  name: string | null
  reason: string | null
  purpose: string | null
}

function normalizeContact(value: unknown): ContactDetail | null {
  if (!isRecord(value)) return null
  const channelValue = value['channel']
  const addressValue = value['address']
  const nameValue = value['name']
  const reasonValue = value['reason']
  const purposeValue = value['purpose']
  const channel = typeof channelValue === 'string' && channelValue.trim().length ? channelValue : null
  const address = typeof addressValue === 'string' && addressValue.trim().length ? addressValue : null
  const name = typeof nameValue === 'string' && nameValue.trim().length ? nameValue : null
  const reason = typeof reasonValue === 'string' && reasonValue.trim().length ? reasonValue : null
  const purpose = typeof purposeValue === 'string' && purposeValue.trim().length ? purposeValue : null
  return { channel, address, name, reason, purpose }
}

function formatChannelLabel(channel: string | null): string | null {
  if (!channel) return null
  switch (channel.toLowerCase()) {
    case 'email':
      return 'Email'
    case 'sms':
      return 'SMS text'
    default:
      return channel
  }
}

type CredentialDetail = {
  name: string | null
  key: string | null
  domainPattern: string | null
  description: string | null
}

type HumanInputOptionDetail = {
  key: string | null
  title: string | null
  description: string | null
}

type HumanInputRequestDetail = {
  question: string | null
  options: HumanInputOptionDetail[]
}

function normalizeCredential(value: unknown): CredentialDetail | null {
  if (!isRecord(value)) return null
  const nameValue = value['name']
  const keyValue = value['key']
  const domainValue = value['domain_pattern']
  const descriptionValue = value['description']
  const name = typeof nameValue === 'string' && nameValue.trim().length ? nameValue : null
  const key = typeof keyValue === 'string' && keyValue.trim().length ? keyValue : null
  const domainPattern = typeof domainValue === 'string' && domainValue.trim().length ? domainValue : null
  const description = typeof descriptionValue === 'string' && descriptionValue.trim().length ? descriptionValue : null
  return { name, key, domainPattern, description }
}

function normalizeHumanInputOption(value: unknown): HumanInputOptionDetail | null {
  if (!isRecord(value)) return null
  const keyValue = value['key'] ?? value['option_key'] ?? value['optionKey']
  const titleValue = value['title']
  const descriptionValue = value['description']
  const key = typeof keyValue === 'string' && keyValue.trim().length ? keyValue : null
  const title = typeof titleValue === 'string' && titleValue.trim().length ? titleValue : null
  const description = typeof descriptionValue === 'string' && descriptionValue.trim().length ? descriptionValue : null
  return { key, title, description }
}

function normalizeHumanInputRequest(value: unknown): HumanInputRequestDetail | null {
  if (!isRecord(value)) return null
  const questionValue = value['question']
  const question = typeof questionValue === 'string' && questionValue.trim().length ? questionValue : null
  const optionsValue = value['options']
  const options = Array.isArray(optionsValue)
    ? (optionsValue.map(normalizeHumanInputOption).filter(Boolean) as HumanInputOptionDetail[])
    : []
  if (!question) return null
  return { question, options }
}

export function RequestHumanInputDetail({ entry }: ToolDetailProps) {
  const params = (entry.parameters as Record<string, unknown>) || {}
  const singleQuestion = typeof params['question'] === 'string' ? params['question'] : null
  const singleOptionsRaw = params['options']
  const singleOptions = Array.isArray(singleOptionsRaw)
    ? (singleOptionsRaw.map(normalizeHumanInputOption).filter(Boolean) as HumanInputOptionDetail[])
    : []
  const batchRequestsRaw = Array.isArray(params['requests']) ? params['requests'] : []
  const requests = batchRequestsRaw.length
    ? (batchRequestsRaw.map(normalizeHumanInputRequest).filter(Boolean) as HumanInputRequestDetail[])
    : singleQuestion
      ? [{ question: singleQuestion, options: singleOptions }]
      : []

  const result = parseResultObject(entry.result)
  const targetChannel =
    typeof result?.['target_channel'] === 'string'
      ? (result['target_channel'] as string)
      : typeof result?.['targetChannel'] === 'string'
        ? (result['targetChannel'] as string)
        : null

  const infoItems: Array<{ label: string; value: ReactNode } | null> = [
    targetChannel ? { label: 'Channel', value: formatChannelLabel(targetChannel) || targetChannel } : null,
  ]

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <KeyValueList items={infoItems} />
      {requests.length ? (
        <Section title={`Question${requests.length === 1 ? '' : 's'}`}>
          <ol className="space-y-3">
            {requests.map((request, requestIndex) => (
              <ToolResultCard as="li" key={`human-input-request-${requestIndex}`} className="bg-white/90 p-3">
                <p className="whitespace-pre-line font-semibold text-slate-800">
                  {requests.length > 1 ? `${requestIndex + 1}. ` : ''}{request.question}
                </p>
                {request.options.length ? (
                  <ol className="mt-3 space-y-3">
                    {request.options.map((option, optionIndex) => (
                      <li key={option.key || `human-input-option-${requestIndex}-${optionIndex}`}>
                        <p className="font-semibold text-slate-800">
                          {optionIndex + 1}. {option.title || `Option ${optionIndex + 1}`}
                        </p>
                        {option.description ? (
                          <p className="mt-1 whitespace-pre-line text-slate-600">{option.description}</p>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                ) : null}
              </ToolResultCard>
            ))}
          </ol>
        </Section>
      ) : null}
    </div>
  )
}

export function RequestContactPermissionDetail({ entry }: ToolDetailProps) {
  const params = (entry.parameters as Record<string, unknown>) || {}
  const contactsRaw = params['contacts']
  const contacts = Array.isArray(contactsRaw)
    ? (contactsRaw.map(normalizeContact).filter(Boolean) as ContactDetail[])
    : []

  const result = parseResultObject(entry.result)
  const statusValue = typeof result?.['status'] === 'string' ? (result['status'] as string) : null
  const messageValue = typeof result?.['message'] === 'string' ? (result['message'] as string) : null
  const createdCount = typeof result?.['created_count'] === 'number' ? (result['created_count'] as number) : null
  const alreadyAllowed = typeof result?.['already_allowed_count'] === 'number' ? (result['already_allowed_count'] as number) : null
  const alreadyPending = typeof result?.['already_pending_count'] === 'number' ? (result['already_pending_count'] as number) : null
  const approvalRaw = typeof result?.['approval_url'] === 'string' ? (result['approval_url'] as string) : null
  const approvalUrl = approvalRaw && /^https?:\/\//i.test(approvalRaw) ? approvalRaw : null
  const statusLabel = statusValue ? statusValue.toUpperCase() : null
  const messageText = isNonEmptyString(messageValue) ? messageValue : entry.summary || entry.caption || null

  const infoItems: Array<{ label: string; value: ReactNode } | null> = [
    statusLabel ? { label: 'Status', value: statusLabel } : null,
    createdCount !== null ? { label: 'Created requests', value: createdCount } : null,
    alreadyAllowed !== null ? { label: 'Already allowed', value: alreadyAllowed } : null,
    alreadyPending !== null ? { label: 'Already pending', value: alreadyPending } : null,
    approvalRaw
      ? {
          label: 'Approval link',
          value: approvalUrl ? (
            <ExternalLinkText href={approvalUrl}>{approvalRaw}</ExternalLinkText>
          ) : (
            approvalRaw
          ),
        }
      : null,
  ]

  return (
    <div className="space-y-4 text-sm text-slate-600">
      {messageText ? <p className="whitespace-pre-line text-slate-700">{messageText}</p> : null}
      <KeyValueList items={infoItems} />
      {contacts.length ? (
        <Section title={`Contact request${contacts.length === 1 ? '' : 's'}`}>
          <ol className="space-y-3">
            {contacts.map((contact, index) => {
              const channelLabel = formatChannelLabel(contact.channel)
              const heading = contact.name || contact.address || `Contact ${index + 1}`
              const contactItems: Array<{ label: string; value: ReactNode } | null> = [
                channelLabel ? { label: 'Channel', value: channelLabel } : null,
                contact.address && contact.address !== heading ? { label: 'Address', value: contact.address } : null,
                contact.purpose ? { label: 'Purpose', value: contact.purpose } : null,
                contact.reason
                  ? {
                      label: 'Reason',
                      value: <span className="whitespace-pre-line">{contact.reason}</span>,
                    }
                  : null,
              ]
              return (
                <ToolResultCard as="li" key={`contact-${index}`} className="bg-white/90 p-3">
                  <p className="font-semibold text-slate-800">{heading}</p>
                  <KeyValueList items={contactItems} />
                </ToolResultCard>
              )
            })}
          </ol>
        </Section>
      ) : null}
    </div>
  )
}

type SpawnDecision = 'approve' | 'decline'
type SpawnResolution = SpawnDecision | 'expired'

type SpawnDecisionResponse = {
  status?: string
  request_status?: string
  spawned_agent_name?: string
}

const spawnAgentActionsClassName = 'grid w-full grid-cols-1 gap-[0.45rem] min-[561px]:grid-cols-2'
const spawnAgentActionButtonBaseClassName = 'inline-flex w-full min-w-0 cursor-pointer items-center justify-center rounded-[0.65rem] border border-transparent px-[0.75rem] py-[0.43rem] text-[0.71rem] font-[650] tracking-[0.01em] transition-all duration-[160ms] focus-visible:outline-none focus-visible:shadow-[0_0_0_3px_rgba(99,102,241,0.18)] disabled:cursor-not-allowed disabled:opacity-[0.72]'
const spawnAgentPrimaryButtonClassName = 'border-[rgba(79,70,229,0.42)] bg-[linear-gradient(180deg,#6366f1_0%,#4f46e5_100%)] text-white shadow-[0_1px_2px_rgba(79,70,229,0.2)] enabled:hover:border-[rgba(67,56,202,0.5)] enabled:hover:bg-[linear-gradient(180deg,#5b5fe9_0%,#4338ca_100%)]'
const spawnAgentSecondaryButtonClassName = 'border-[rgba(148,163,184,0.35)] bg-[rgba(255,255,255,0.92)] text-[#475569] shadow-[0_1px_2px_rgba(15,23,42,0.05)] enabled:hover:border-[rgba(99,115,141,0.4)] enabled:hover:bg-[rgba(248,250,252,0.96)] enabled:hover:text-[#334155]'
const spawnAgentResolutionBaseClassName = 'flex w-full items-center justify-center rounded-[0.65rem] border border-dashed px-[0.75rem] py-[0.6rem]'
const spawnAgentCreatedResolutionClassName = 'border-[rgba(16,185,129,0.32)] bg-[linear-gradient(180deg,rgba(236,253,245,0.9)_0%,rgba(240,253,250,0.92)_100%)]'
const spawnAgentDeclinedResolutionClassName = 'border-[rgba(148,163,184,0.42)] bg-[linear-gradient(180deg,rgba(248,250,252,0.95)_0%,rgba(241,245,249,0.95)_100%)]'

function parseErrorMessage(error: unknown): string {
  if (error instanceof HttpError) {
    return 'Something went wrong. Please try again.'
  }
  return 'Something went wrong. Please try again.'
}

export function SpawnAgentDetail({ entry }: ToolDetailProps) {
  const queryClient = useQueryClient()
  const params = (entry.parameters as Record<string, unknown>) || {}
  const result = parseResultObject(entry.result)
  const charterRaw = typeof params['charter'] === 'string' ? (params['charter'] as string) : null

  const decisionRaw = typeof result?.['decision_api_url'] === 'string' ? (result['decision_api_url'] as string) : null
  const decisionApiUrl =
    decisionRaw && (/^https?:\/\//i.test(decisionRaw) || decisionRaw.startsWith('/')) ? decisionRaw : null
  const initialStatus =
    typeof result?.['request_status'] === 'string'
      ? (result['request_status'] as string)
      : typeof result?.['status'] === 'string'
        ? (result['status'] as string)
        : 'pending'
  const [requestStatus, setRequestStatus] = useState(initialStatus.toLowerCase())
  const [busyDecision, setBusyDecision] = useState<SpawnDecision | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const normalizedStatus = requestStatus.toLowerCase()
  const resolvedDecision: SpawnResolution | null =
    normalizedStatus === 'approved'
      ? 'approve'
      : normalizedStatus === 'rejected' || normalizedStatus === 'declined'
        ? 'decline'
        : normalizedStatus === 'expired'
          ? 'expired'
        : null
  const showActions = false
  const actionsLocked = Boolean(busyDecision)

  const submitDecision = async (decision: SpawnDecision) => {
    if (!decisionApiUrl || actionsLocked) return
    setBusyDecision(decision)
    setActionError(null)

    try {
      const response = await jsonRequest<SpawnDecisionResponse>(decisionApiUrl, {
        method: 'POST',
        includeCsrf: true,
        json: { decision },
      })
      const responseStatus =
        typeof response?.request_status === 'string' ? response.request_status.toLowerCase() : null
      if (responseStatus) {
        setRequestStatus(responseStatus)
      }
      if (decision === 'approve') {
        void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
      }
    } catch (error) {
      setActionError(parseErrorMessage(error))
    } finally {
      setBusyDecision(null)
    }
  }

  useEffect(() => {
    if (!decisionApiUrl) return
    let cancelled = false

    const fetchLatestStatus = async () => {
      try {
        const response = await jsonRequest<SpawnDecisionResponse>(decisionApiUrl, { method: 'GET' })
        if (cancelled) return
        const responseStatus =
          typeof response?.request_status === 'string' ? response.request_status.toLowerCase() : null
        if (responseStatus) {
          setRequestStatus(responseStatus)
        }
      } catch {
        // Ignore passive status refresh errors; user actions already show explicit feedback.
      }
    }

    void fetchLatestStatus()
    return () => {
      cancelled = true
    }
  }, [decisionApiUrl])

  return (
    <div className="space-y-4 text-sm text-slate-600">
      {charterRaw ? (
        <Section title="Charter">
          <p className="whitespace-pre-line text-slate-700">{charterRaw}</p>
        </Section>
      ) : null}
      {showActions ? (
        <div className={spawnAgentActionsClassName}>
          <button
            type="button"
            onClick={() => void submitDecision('approve')}
            disabled={actionsLocked}
            className={`${spawnAgentActionButtonBaseClassName} ${spawnAgentPrimaryButtonClassName}`}
          >
            {busyDecision === 'approve' ? 'Creating...' : 'Create'}
          </button>
          <button
            type="button"
            onClick={() => void submitDecision('decline')}
            disabled={actionsLocked}
            className={`${spawnAgentActionButtonBaseClassName} ${spawnAgentSecondaryButtonClassName}`}
          >
            {busyDecision === 'decline' ? 'Declining...' : 'Decline'}
          </button>
        </div>
      ) : null}
      {resolvedDecision ? (
        <div
          className={`${spawnAgentResolutionBaseClassName} ${resolvedDecision === 'approve' ? spawnAgentCreatedResolutionClassName : spawnAgentDeclinedResolutionClassName}`}
        >
          <span className="m-0 text-[0.72rem] font-semibold text-[#334155]">
            {resolvedDecision === 'approve' ? 'Created' : resolvedDecision === 'expired' ? 'Expired' : 'Declined'}
          </span>
        </div>
      ) : null}
      {actionError ? <p className="m-0 text-[0.72rem] text-[#be123c]">{actionError}</p> : null}
    </div>
  )
}

export function SecureCredentialsDetail({ entry }: ToolDetailProps) {
  const params = (entry.parameters as Record<string, unknown>) || {}
  const credentialsRaw = params['credentials']
  const credentials = Array.isArray(credentialsRaw)
    ? (credentialsRaw.map(normalizeCredential).filter(Boolean) as CredentialDetail[])
    : []

  const result = parseResultObject(entry.result)
  const messageValue = typeof result?.['message'] === 'string' ? (result['message'] as string) : null
  const createdCount = typeof result?.['created_count'] === 'number' ? (result['created_count'] as number) : null
  const errorsRaw = Array.isArray(result?.['errors']) ? (result?.['errors'] as unknown[]) : []
  const errors = errorsRaw
    .map((error) => (typeof error === 'string' ? error : stringify(error)))
    .filter((value): value is string => Boolean(value && value.trim()))
  const messageText = isNonEmptyString(messageValue) ? messageValue : entry.summary || entry.caption || null
  const submissionUrl = extractFirstUrl(messageText)

  const infoItems: Array<{ label: string; value: ReactNode } | null> = [
    createdCount !== null ? { label: 'Created requests', value: createdCount } : null,
    submissionUrl
      ? {
          label: 'Submission link',
          value: (
            <ExternalLinkText href={submissionUrl}>{submissionUrl}</ExternalLinkText>
          ),
        }
      : null,
  ]

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <KeyValueList items={infoItems} />
      {errors.length ? (
        <Section title="Errors">
          <ul className="list-disc space-y-1 pl-5 text-sm text-rose-600">
            {errors.map((error, index) => (
              <li key={`error-${index}`}>{error}</li>
            ))}
          </ul>
        </Section>
      ) : null}
      {credentials.length ? (
        <Section title={`Credential${credentials.length === 1 ? '' : 's'} requested`}>
          <ol className="space-y-3">
            {credentials.map((credential, index) => {
              const credentialItems: Array<{ label: string; value: ReactNode } | null> = [
                credential.key ? { label: 'Key', value: credential.key } : null,
                credential.domainPattern ? { label: 'Domain', value: credential.domainPattern } : null,
                credential.description
                  ? {
                      label: 'Description',
                      value: <span className="whitespace-pre-line">{credential.description}</span>,
                    }
                  : null,
              ]
              return (
                <ToolResultCard as="li" key={`credential-${index}`} className="bg-white/90 p-3">
                  <KeyValueList items={credentialItems} />
                </ToolResultCard>
              )
            })}
          </ol>
        </Section>
      ) : null}
    </div>
  )
}
