export const EMAIL_SENDING_MODE_OPTIONS = [
  {
    value: 'review_all_external',
    title: 'Review before send',
    description: 'Every permitted email to someone outside your verified workspace waits in Outbox.',
  },
  {
    value: 'review_new_contacts',
    title: 'Review only new contacts',
    description: 'Known contacts send immediately; newly auto-approved external recipients wait for review.',
  },
  {
    value: 'send_automatically',
    title: 'Send automatically',
    description: 'Permitted external email is sent without Outbox review. Contact approval still applies.',
  },
] as const

export type EmailSendingMode = (typeof EMAIL_SENDING_MODE_OPTIONS)[number]['value']

export const EMAIL_SENDING_MODE_STRICTNESS: Record<EmailSendingMode, number> = {
  send_automatically: 0,
  review_new_contacts: 1,
  review_all_external: 2,
}
