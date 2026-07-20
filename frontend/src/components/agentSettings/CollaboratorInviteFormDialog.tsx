import { useEffect, useState, type FormEvent } from 'react'
import { Mail, UserPlus } from 'lucide-react'

import { FormField, TextInput } from '../common/FormControls'
import { ImmersiveDialog } from '../common/ImmersiveDialog'
import { InlineStatusBanner } from '../common/InlineStatusBanner'
import { SettingsActionButton } from './SettingsControls'

type CollaboratorInviteFormDialogProps = {
  open: boolean
  formId: string
  emailFieldId: string
  title: string
  subtitle: string
  onSubmit: (email: string) => Promise<string | void> | string | void
  onClose: () => void
  submitLabel: string
  submittingLabel: string
  cancelLabel?: string
  emptyEmailMessage?: string
  disabled?: boolean
  disabledMessage?: string
  fallbackErrorMessage?: string
  closeOnSuccess?: boolean
  autoFocus?: boolean
}

export function CollaboratorInviteFormDialog({
  open,
  formId,
  emailFieldId,
  title,
  subtitle,
  onSubmit,
  onClose,
  submitLabel,
  submittingLabel,
  cancelLabel,
  emptyEmailMessage,
  disabled = false,
  disabledMessage,
  fallbackErrorMessage = 'Unable to send invite.',
  closeOnSuccess = false,
  autoFocus = false,
}: CollaboratorInviteFormDialogProps) {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setEmail('')
      setError(null)
      setSuccess(null)
    }
  }, [open])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedEmail = email.trim().toLowerCase()
    if (!normalizedEmail) {
      if (emptyEmailMessage) setError(emptyEmailMessage)
      return
    }

    setBusy(true)
    setError(null)
    setSuccess(null)
    try {
      const message = await onSubmit(normalizedEmail)
      if (closeOnSuccess) onClose()
      else {
        setSuccess(message || `Invite sent to ${normalizedEmail}.`)
        setEmail('')
      }
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : fallbackErrorMessage)
    } finally {
      setBusy(false)
    }
  }

  const actions = (
    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
      {cancelLabel ? (
        <SettingsActionButton surface="standalone" responsive disabled={busy} onClick={onClose}>
          {cancelLabel}
        </SettingsActionButton>
      ) : null}
      <SettingsActionButton
        type="submit"
        form={formId}
        surface="standalone"
        tone="success"
        responsive
        disabled={disabled || !email.trim() || busy}
      >
        {busy ? submittingLabel : submitLabel}
      </SettingsActionButton>
    </div>
  )

  return (
    <ImmersiveDialog
      open={open}
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      icon={UserPlus}
      ariaLabel={title}
      desktopIconBgClass="bg-emerald-100"
      desktopIconColorClass="text-emerald-600"
      desktopWidthClass="sm:max-w-lg"
      desktopBodyClassName="space-y-4"
      footer={actions}
    >
      <form id={formId} className="space-y-3" onSubmit={handleSubmit}>
        {disabledMessage ? <InlineStatusBanner variant="warning">{disabledMessage}</InlineStatusBanner> : null}
        <FormField id={emailFieldId} label="Collaborator email">
          <div className="relative mt-1">
            <Mail className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
            <TextInput
              id={emailFieldId}
              type="email"
              autoFocus={autoFocus}
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.currentTarget.value)}
              className="mt-0 pl-9 focus:border-emerald-500 focus:ring-emerald-500 disabled:cursor-not-allowed disabled:bg-white"
              placeholder="name@company.com"
              disabled={disabled || busy}
            />
          </div>
        </FormField>
        {error ? <InlineStatusBanner variant="error" role="alert" density="compact">{error}</InlineStatusBanner> : null}
        {success ? <InlineStatusBanner variant="success" role="status" density="compact">{success}</InlineStatusBanner> : null}
      </form>
    </ImmersiveDialog>
  )
}
