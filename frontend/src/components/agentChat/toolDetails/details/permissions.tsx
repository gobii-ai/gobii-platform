import type { ReactNode } from 'react'

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
