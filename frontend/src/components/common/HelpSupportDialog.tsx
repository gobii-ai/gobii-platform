import { useEffect, useState } from 'react'
import { CircleHelp } from 'lucide-react'

import { HttpError } from '../../api/http'
import { sendAppSupportRequest } from '../../api/support'
import type { ConsoleContext } from '../../api/context'
import { TextareaSubmitDialog } from './TextareaSubmitDialog'

const SUPPORT_MESSAGE_MAX_LENGTH = 4000

type HelpSupportDialogProps = {
  open: boolean
  onClose: () => void
  agentId?: string | null
  agentName?: string | null
  workspaceContext?: ConsoleContext | null
}

function getSupportErrorMessage(error: unknown): string {
  if (error instanceof HttpError) {
    const body = error.body
    if (
      body
      && typeof body === 'object'
      && 'message' in body
      && typeof body.message === 'string'
      && body.message.trim()
    ) {
      return body.message
    }
  }
  return 'Unable to send your message. Please try again.'
}

export function HelpSupportDialog({
  open,
  onClose,
  agentId = null,
  agentName = null,
  workspaceContext = null,
}: HelpSupportDialogProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(false)

  useEffect(() => {
    if (open) {
      setError(null)
      setSubmitted(false)
      setBusy(false)
    }
  }, [open])

  const handleSubmit = async (trimmedMessage: string) => {
    if (!trimmedMessage) {
      setError('Message is required.')
      return
    }

    setBusy(true)
    setError(null)
    try {
      await sendAppSupportRequest({
        message: trimmedMessage,
        pageUrl: typeof window === 'undefined' ? undefined : window.location.href,
        agentId,
        agentName,
        workspaceContext,
      })
      setSubmitted(true)
    } catch (err) {
      setError(getSupportErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <TextareaSubmitDialog
      open={open}
      title="Contact support"
      subtitle="Send us a note and we'll follow up by email."
      icon={CircleHelp}
      textareaId="app-support-message"
      label="How can we help?"
      placeholder="Tell us what happened or what you need help with."
      maxLength={SUPPORT_MESSAGE_MAX_LENGTH}
      minHeightClassName="min-h-32"
      busy={busy}
      error={error}
      successMessage={submitted ? "Thanks, we'll follow up by email." : null}
      submitLabel="Send message"
      busyLabel="Sending..."
      submitDisabledWhenEmpty
      autoFocus
      onClose={onClose}
      onSubmit={handleSubmit}
      onErrorClear={() => {
        if (error) {
          setError(null)
        }
      }}
    />
  )
}
