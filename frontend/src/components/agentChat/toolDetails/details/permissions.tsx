import { useEffect, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { HttpError, jsonRequest } from '../../../../api/http'
import type { ToolDetailProps } from '../../tooling/types'
import { isRecord, parseResultObject } from '../../../../util/objectUtils'
import { KeyValueList, Section } from '../shared'
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

type HumanInputRelayPayloadDetail = {
  kind: string | null
  toolName: string | null
  toAddress: string | null
  toNumber: string | null
  subject: string | null
  body: string | null
  bodyText: string | null
  message: string | null
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

function normalizeHumanInputRelayPayload(value: unknown): HumanInputRelayPayloadDetail | null {
  if (!isRecord(value)) return null
  const kindValue = value['kind']
  const toolNameValue = value['tool_name'] ?? value['toolName']
  const toAddressValue = value['to_address'] ?? value['toAddress']
  const toNumberValue = value['to_number'] ?? value['toNumber']
  const subjectValue = value['subject']
  const bodyValue = value['body']
  const bodyTextValue = value['body_text'] ?? value['bodyText']
  const messageValue = value['message']
  const kind = typeof kindValue === 'string' && kindValue.trim().length ? kindValue : null
  const toolName = typeof toolNameValue === 'string' && toolNameValue.trim().length ? toolNameValue : null
  const toAddress = typeof toAddressValue === 'string' && toAddressValue.trim().length ? toAddressValue : null
  const toNumber = typeof toNumberValue === 'string' && toNumberValue.trim().length ? toNumberValue : null
  const subject = typeof subjectValue === 'string' && subjectValue.trim().length ? subjectValue : null
  const body = typeof bodyValue === 'string' && bodyValue.trim().length ? bodyValue : null
  const bodyText = typeof bodyTextValue === 'string' && bodyTextValue.trim().length ? bodyTextValue : null
  const message = typeof messageValue === 'string' && messageValue.trim().length ? messageValue : null
  return { kind, toolName, toAddress, toNumber, subject, body, bodyText, message }
}

export function RequestHumanInputDetail({ entry }: ToolDetailProps) {
  const params = (entry.parameters as Record<string, unknown>) || {}
  const question = typeof params['question'] === 'string' ? params['question'] : null
  const requestsRaw = Array.isArray(params['requests']) ? params['requests'] : []
  const optionsRaw = params['options']
  const options = Array.isArray(optionsRaw)
    ? (optionsRaw.map(normalizeHumanInputOption).filter(Boolean) as HumanInputOptionDetail[])
    : []

  const result = parseResultObject(entry.result)
  const statusValue = typeof result?.['status'] === 'string' ? (result['status'] as string) : null
  const messageValue = typeof result?.['message'] === 'string' ? (result['message'] as string) : null
  const relayMode =
    typeof result?.['relay_mode'] === 'string'
      ? (result['relay_mode'] as string)
      : typeof result?.['relayMode'] === 'string'
        ? (result['relayMode'] as string)
        : null
  const targetChannel =
    typeof result?.['target_channel'] === 'string'
      ? (result['target_channel'] as string)
      : typeof result?.['targetChannel'] === 'string'
        ? (result['targetChannel'] as string)
        : null
  const targetAddress =
    typeof result?.['target_address'] === 'string'
      ? (result['target_address'] as string)
      : typeof result?.['targetAddress'] === 'string'
        ? (result['targetAddress'] as string)
        : null
  const relayPayload = normalizeHumanInputRelayPayload(result?.['relay_payload'] ?? result?.['relayPayload'])
  const inputMode =
    typeof result?.['input_mode'] === 'string'
      ? (result['input_mode'] as string)
      : typeof result?.['inputMode'] === 'string'
        ? (result['inputMode'] as string)
        : options.length > 0
          ? 'options_plus_text'
          : 'free_text_only'
  const selectedOptionTitle =
    typeof result?.['selected_option_title'] === 'string'
      ? (result['selected_option_title'] as string)
      : typeof result?.['selectedOptionTitle'] === 'string'
        ? (result['selectedOptionTitle'] as string)
        : null
  const freeText =
    typeof result?.['free_text'] === 'string'
      ? (result['free_text'] as string)
      : typeof result?.['freeText'] === 'string'
        ? (result['freeText'] as string)
        : null
  const rawReply =
    typeof result?.['raw_reply_text'] === 'string'
      ? (result['raw_reply_text'] as string)
      : typeof result?.['rawReplyText'] === 'string'
        ? (result['rawReplyText'] as string)
        : null
  const relayModeLabel =
    relayMode === 'panel_only'
      ? 'Visible in web panel'
      : relayMode === 'explicit_send_required'
        ? `Needs ${relayPayload?.toolName || 'explicit send'}`
        : null
  const relayPreview = relayPayload?.bodyText || relayPayload?.body || relayPayload?.message || null

  const infoItems: Array<{ label: string; value: ReactNode } | null> = [
    statusValue ? { label: 'Status', value: statusValue.toUpperCase() } : null,
    { label: 'Mode', value: inputMode === 'free_text_only' ? 'Free text only' : 'Options + free text' },
    relayModeLabel ? { label: 'Relay', value: relayModeLabel } : null,
    targetChannel ? { label: 'Channel', value: formatChannelLabel(targetChannel) || targetChannel } : null,
    targetAddress ? { label: 'Recipient', value: targetAddress } : null,
    selectedOptionTitle ? { label: 'Selected option', value: selectedOptionTitle } : null,
  ]

  const introText = isNonEmptyString(messageValue)
    ? messageValue
    : question
      ? question
      : requestsRaw.length > 1
        ? `${requestsRaw.length} questions requested`
      : entry.summary || entry.caption || null

  return (
    <div className="space-y-4 text-sm text-slate-600">
      {introText ? <p className="whitespace-pre-line text-slate-700">{introText}</p> : null}
      <KeyValueList items={infoItems} />
      {options.length > 0 ? (
        <Section title={`Option${options.length === 1 ? '' : 's'}`}>
          <ol className="space-y-3">
            {options.map((option, index) => (
              <li key={option.key || `human-input-option-${index}`} className="rounded-lg border border-slate-200/80 bg-white/90 p-3 shadow-sm">
                <p className="font-semibold text-slate-800">
                  {index + 1}. {option.title || `Option ${index + 1}`}
                </p>
                {option.description ? (
                  <p className="mt-1 whitespace-pre-line text-slate-600">{option.description}</p>
                ) : null}
              </li>
            ))}
          </ol>
        </Section>
      ) : null}
      {freeText ? (
        <Section title="Captured free-text answer">
          <p className="whitespace-pre-line text-slate-700">{freeText}</p>
        </Section>
      ) : null}
      {rawReply && rawReply !== freeText ? (
        <Section title="Raw reply">
          <p className="whitespace-pre-line text-slate-700">{rawReply}</p>
        </Section>
      ) : null}
      {relayPayload && !selectedOptionTitle && !freeText ? (
        <Section title="Relay guidance">
          <div className="space-y-3">
            {relayPayload.subject ? (
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">Subject</p>
                <p className="mt-1 whitespace-pre-line text-slate-700">{relayPayload.subject}</p>
              </div>
            ) : null}
            {relayPreview ? (
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">Preview</p>
                <p className="mt-1 whitespace-pre-line text-slate-700">{relayPreview}</p>
              </div>
            ) : null}
          </div>
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
            <a href={approvalUrl} target="_blank" rel="noopener noreferrer" className="text-indigo-600 underline">
              {approvalRaw}
            </a>
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
                <li key={`contact-${index}`} className="rounded-lg border border-slate-200/80 bg-white/90 p-3 shadow-sm">
                  <p className="font-semibold text-slate-800">{heading}</p>
                  <KeyValueList items={contactItems} />
                </li>
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
  const showActions = Boolean(decisionApiUrl) && resolvedDecision === null
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
        <div className="spawn-agent-actions">
          <button
            type="button"
            onClick={() => void submitDecision('approve')}
            disabled={actionsLocked}
            className="spawn-agent-action-btn spawn-agent-action-btn--primary"
          >
            {busyDecision === 'approve' ? 'Creating...' : 'Create'}
          </button>
          <button
            type="button"
            onClick={() => void submitDecision('decline')}
            disabled={actionsLocked}
            className="spawn-agent-action-btn spawn-agent-action-btn--secondary"
          >
            {busyDecision === 'decline' ? 'Declining...' : 'Decline'}
          </button>
        </div>
      ) : null}
      {resolvedDecision ? (
        <div
          className={`spawn-agent-resolution ${resolvedDecision === 'approve' ? 'spawn-agent-resolution--created' : 'spawn-agent-resolution--declined'}`}
        >
          <span className="spawn-agent-resolution-text">
            {resolvedDecision === 'approve' ? 'Created' : resolvedDecision === 'expired' ? 'Expired' : 'Declined'}
          </span>
        </div>
      ) : null}
      {actionError ? <p className="spawn-agent-action-error">{actionError}</p> : null}
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
            <a href={submissionUrl} target="_blank" rel="noopener noreferrer" className="text-indigo-600 underline">
              {submissionUrl}
            </a>
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
                <li key={`credential-${index}`} className="rounded-lg border border-slate-200/80 bg-white/90 p-3 shadow-sm">
                  <KeyValueList items={credentialItems} />
                </li>
              )
            })}
          </ol>
        </Section>
      ) : null}
    </div>
  )
}
