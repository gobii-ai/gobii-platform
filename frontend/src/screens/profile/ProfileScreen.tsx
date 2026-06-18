import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircle2,
  Copy,
  FileText,
  Mail,
  Phone,
  RefreshCcw,
  Save,
  ShieldCheck,
  Trash2,
  User,
  XCircle,
} from 'lucide-react'

import {
  addUserPhone,
  deleteUserPhone,
  resendEmailVerification,
  resendUserPhone,
  verifyUserPhone,
} from '../../api/agentSetup'
import type { PhoneState } from '../../api/agentSetup'
import { HttpError } from '../../api/http'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { updateUserCustomInstructions, updateUserProfile } from '../../api/userProfile'
import type { UserProfileFormState, UserProfilePayload } from '../../api/userProfile'

type ProfileScreenProps = {
  initialData: UserProfilePayload
}

type ProfileFieldErrors = Partial<Record<keyof UserProfileFormState | 'profile' | 'customInstructions' | 'nonFieldErrors', string[]>>

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
}

function extractProfileErrors(error: unknown): ProfileFieldErrors {
  if (!(error instanceof HttpError)) {
    return {}
  }
  const body = asRecord(error.body)
  const errors = asRecord(body?.errors)
  if (!errors) {
    return {}
  }
  return Object.fromEntries(
    Object.entries(errors).filter((entry): entry is [keyof ProfileFieldErrors, string[]] => (
      Array.isArray(entry[1]) && entry[1].every((item) => typeof item === 'string')
    )),
  )
}

function firstError(errors: ProfileFieldErrors, field: keyof ProfileFieldErrors): string | null {
  const value = errors[field]
  return value?.[0] ?? null
}

function formatDateTime(value: string | null): string | null {
  if (!value) {
    return null
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return null
  }
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(parsed)
}

function EmailVerificationSection({
  email,
  isVerified,
  onVerifiedChange,
}: {
  email: string
  isVerified: boolean
  onVerifiedChange: (verified: boolean) => void
}) {
  const [sending, setSending] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleResend = useCallback(async () => {
    setSending(true)
    setMessage(null)
    setError(null)
    try {
      const result = await resendEmailVerification()
      onVerifiedChange(result.verified)
      setMessage(result.message)
    } catch (err) {
      setError(safeErrorMessage(err))
    } finally {
      setSending(false)
    }
  }, [onVerifiedChange])

  return (
    <section className="profile-screen__section">
      <div className="profile-screen__section-header">
        <div className="profile-screen__section-icon" aria-hidden="true">
          <Mail className="h-4 w-4" />
        </div>
        <div>
          <h2>Email Verification</h2>
          <p>{email || 'No email address on file'}</p>
        </div>
      </div>
      <div className="profile-screen__status-row">
        {isVerified ? (
          <span className="profile-screen__status profile-screen__status--success">
            <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            Verified
          </span>
        ) : (
          <span className="profile-screen__status profile-screen__status--warning">
            <XCircle className="h-4 w-4" aria-hidden="true" />
            Unverified
          </span>
        )}
        {!isVerified ? (
          <button
            type="button"
            className="profile-screen__button profile-screen__button--secondary"
            onClick={handleResend}
            disabled={sending}
          >
            <RefreshCcw className="h-4 w-4" aria-hidden="true" />
            {sending ? 'Sending...' : 'Resend'}
          </button>
        ) : null}
      </div>
      {message ? <p className="profile-screen__feedback profile-screen__feedback--success">{message}</p> : null}
      {error ? <p className="profile-screen__feedback profile-screen__feedback--error">{error}</p> : null}
    </section>
  )
}

function PhoneSection({
  phone,
  onPhoneChange,
}: {
  phone: PhoneState | null
  onPhoneChange: (phone: PhoneState | null) => void
}) {
  const [phoneNumber, setPhoneNumber] = useState('')
  const [verificationCode, setVerificationCode] = useState('')
  const [busyAction, setBusyAction] = useState<'add' | 'verify' | 'resend' | 'delete' | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const verifiedAt = formatDateTime(phone?.verifiedAt ?? null)
  const resendDisabled = Boolean(phone?.cooldownRemaining && phone.cooldownRemaining > 0)

  const runPhoneAction = useCallback(async (
    action: 'add' | 'verify' | 'resend' | 'delete',
    fn: () => Promise<{ phone: PhoneState | null }>,
    successMessage: string,
  ) => {
    setBusyAction(action)
    setMessage(null)
    setError(null)
    try {
      const result = await fn()
      onPhoneChange(result.phone)
      setMessage(successMessage)
      if (action === 'add') {
        setPhoneNumber('')
      }
      if (action === 'verify') {
        setVerificationCode('')
      }
    } catch (err) {
      setError(safeErrorMessage(err))
    } finally {
      setBusyAction(null)
    }
  }, [onPhoneChange])

  return (
    <section className="profile-screen__section">
      <div className="profile-screen__section-header">
        <div className="profile-screen__section-icon" aria-hidden="true">
          <Phone className="h-4 w-4" />
        </div>
        <div>
          <h2>Phone Number</h2>
          <p>Used for SMS verification and agent texting.</p>
        </div>
      </div>

      {!phone ? (
        <form
          className="profile-screen__inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            const normalized = phoneNumber.trim()
            if (!normalized) {
              setError('Phone number is required.')
              return
            }
            void runPhoneAction('add', () => addUserPhone(normalized), 'Verification code sent.')
          }}
        >
          <label className="profile-screen__field">
            <span>SMS Number</span>
            <input
              type="tel"
              value={phoneNumber}
              onChange={(event) => setPhoneNumber(event.target.value)}
              placeholder="+15551234567"
              autoComplete="tel"
            />
          </label>
          <button
            type="submit"
            className="profile-screen__button profile-screen__button--primary"
            disabled={busyAction === 'add'}
          >
            <Phone className="h-4 w-4" aria-hidden="true" />
            {busyAction === 'add' ? 'Sending...' : 'Add Phone'}
          </button>
        </form>
      ) : (
        <div className="profile-screen__phone-current">
          <div>
            <p className="profile-screen__phone-number">{phone.number}</p>
            {phone.isVerified ? (
              <p className="profile-screen__muted">Verified{verifiedAt ? ` ${verifiedAt}` : ''}</p>
            ) : (
              <p className="profile-screen__muted">Verification pending</p>
            )}
          </div>
          <button
            type="button"
            className="profile-screen__icon-button profile-screen__icon-button--danger"
            onClick={() => void runPhoneAction('delete', deleteUserPhone, 'Phone number removed.')}
            disabled={busyAction === 'delete'}
            aria-label="Remove phone number"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      )}

      {phone && !phone.isVerified ? (
        <form
          className="profile-screen__verify-form"
          onSubmit={(event) => {
            event.preventDefault()
            const code = verificationCode.trim()
            if (!code) {
              setError('Verification code is required.')
              return
            }
            void runPhoneAction('verify', () => verifyUserPhone(code), 'Phone number verified.')
          }}
        >
          <label className="profile-screen__field">
            <span>Verification Code</span>
            <input
              value={verificationCode}
              onChange={(event) => setVerificationCode(event.target.value)}
              placeholder="123456"
              inputMode="numeric"
              autoComplete="one-time-code"
            />
          </label>
          <div className="profile-screen__button-row">
            <button
              type="submit"
              className="profile-screen__button profile-screen__button--primary"
              disabled={busyAction === 'verify'}
            >
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              {busyAction === 'verify' ? 'Verifying...' : 'Verify'}
            </button>
            <button
              type="button"
              className="profile-screen__button profile-screen__button--secondary"
              onClick={() => void runPhoneAction('resend', resendUserPhone, 'Verification code resent.')}
              disabled={busyAction === 'resend' || resendDisabled}
            >
              <RefreshCcw className="h-4 w-4" aria-hidden="true" />
              {resendDisabled ? `Resend in ${phone.cooldownRemaining}s` : 'Resend'}
            </button>
          </div>
        </form>
      ) : null}

      {message ? <p className="profile-screen__feedback profile-screen__feedback--success">{message}</p> : null}
      {error ? <p className="profile-screen__feedback profile-screen__feedback--error">{error}</p> : null}
    </section>
  )
}

export function ProfileScreen({ initialData }: ProfileScreenProps) {
  const [data, setData] = useState(initialData)
  const [draft, setDraft] = useState<UserProfileFormState>(initialData.profile)
  const [errors, setErrors] = useState<ProfileFieldErrors>({})
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [customInstructionsDraft, setCustomInstructionsDraft] = useState(initialData.customInstructions)
  const [customInstructionsErrors, setCustomInstructionsErrors] = useState<string[]>([])
  const [customInstructionsMessage, setCustomInstructionsMessage] = useState<string | null>(null)
  const [savingCustomInstructions, setSavingCustomInstructions] = useState(false)
  const [copyMessage, setCopyMessage] = useState<string | null>(null)
  const referralInputRef = useRef<HTMLInputElement | null>(null)
  const isDirty = useMemo(() => (
    draft.firstName !== data.profile.firstName
    || draft.lastName !== data.profile.lastName
    || draft.timezone !== data.profile.timezone
  ), [data.profile, draft])
  const normalizedCustomInstructionsDraft = useMemo(
    () => customInstructionsDraft.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim(),
    [customInstructionsDraft],
  )
  const customInstructionsChanged = normalizedCustomInstructionsDraft !== data.customInstructions
  const customInstructionsOverLimit = normalizedCustomInstructionsDraft.length > data.customInstructionsMaxChars

  useEffect(() => {
    setData(initialData)
    setDraft(initialData.profile)
    setCustomInstructionsDraft(initialData.customInstructions)
    setCustomInstructionsErrors([])
    setCustomInstructionsMessage(null)
    setErrors({})
    setSaveError(null)
    setSaveMessage(null)
  }, [initialData])

  const updateDraft = useCallback((field: keyof UserProfileFormState, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }))
    setErrors((current) => ({ ...current, [field]: undefined }))
    setSaveError(null)
    setSaveMessage(null)
  }, [])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setErrors({})
    setSaveError(null)
    setSaveMessage(null)
    try {
      const nextData = await updateUserProfile(draft)
      setData(nextData)
      setDraft(nextData.profile)
      setSaveMessage('Profile saved.')
    } catch (err) {
      const fieldErrors = extractProfileErrors(err)
      setErrors(fieldErrors)
      setSaveError(firstError(fieldErrors, 'nonFieldErrors') || safeErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }, [draft])

  const handleCustomInstructionsSave = useCallback(async () => {
    if (normalizedCustomInstructionsDraft.length > data.customInstructionsMaxChars) {
      setCustomInstructionsErrors([
        `Custom instructions must be ${data.customInstructionsMaxChars} characters or fewer.`,
      ])
      return
    }

    setSavingCustomInstructions(true)
    setCustomInstructionsErrors([])
    setCustomInstructionsMessage(null)
    try {
      const nextData = await updateUserCustomInstructions(normalizedCustomInstructionsDraft)
      setData(nextData)
      setCustomInstructionsDraft(nextData.customInstructions)
      setCustomInstructionsMessage('Custom instructions saved.')
    } catch (err) {
      const fieldErrors = extractProfileErrors(err)
      const customError = firstError(fieldErrors, 'customInstructions')
      setCustomInstructionsErrors([customError || safeErrorMessage(err)])
    } finally {
      setSavingCustomInstructions(false)
    }
  }, [data.customInstructionsMaxChars, normalizedCustomInstructionsDraft])

  const handleCopyReferral = useCallback(async () => {
    setCopyMessage(null)
    try {
      await navigator.clipboard.writeText(data.referralLink)
      setCopyMessage('Copied.')
    } catch {
      referralInputRef.current?.select()
      setCopyMessage('Referral link selected.')
    }
  }, [data.referralLink])

  return (
    <div className="profile-screen profile-screen--embedded">
      <header className="profile-screen__header">
        <div className="profile-screen__title-icon" aria-hidden="true">
          <User className="h-5 w-5" />
        </div>
        <div>
          <p className="profile-screen__eyebrow">Account</p>
          <h1>Profile</h1>
        </div>
      </header>

      <section className="profile-screen__section">
        <div className="profile-screen__section-header">
          <div className="profile-screen__section-icon" aria-hidden="true">
            <User className="h-4 w-4" />
          </div>
          <div>
            <h2>Basics</h2>
            <p>Name and timezone used across Gobii.</p>
          </div>
        </div>
        <div className="profile-screen__form-grid">
          <label className="profile-screen__field">
            <span>First Name</span>
            <input
              value={draft.firstName}
              onChange={(event) => updateDraft('firstName', event.target.value)}
              autoComplete="given-name"
            />
            {firstError(errors, 'firstName') ? <em>{firstError(errors, 'firstName')}</em> : null}
          </label>
          <label className="profile-screen__field">
            <span>Last Name</span>
            <input
              value={draft.lastName}
              onChange={(event) => updateDraft('lastName', event.target.value)}
              autoComplete="family-name"
            />
            {firstError(errors, 'lastName') ? <em>{firstError(errors, 'lastName')}</em> : null}
          </label>
          <label className="profile-screen__field profile-screen__field--wide">
            <span>Timezone</span>
            <select
              value={draft.timezone}
              onChange={(event) => updateDraft('timezone', event.target.value)}
            >
              {data.timezoneOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {firstError(errors, 'timezone') ? <em>{firstError(errors, 'timezone')}</em> : null}
          </label>
        </div>
        <div className="profile-screen__actions">
          <button
            type="button"
            className="profile-screen__button profile-screen__button--primary"
            onClick={() => void handleSave()}
            disabled={saving || !isDirty}
          >
            <Save className="h-4 w-4" aria-hidden="true" />
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
          {saveMessage ? <p className="profile-screen__feedback profile-screen__feedback--success">{saveMessage}</p> : null}
          {saveError ? <p className="profile-screen__feedback profile-screen__feedback--error">{saveError}</p> : null}
        </div>
      </section>

      <section className="profile-screen__section">
        <div className="profile-screen__section-header">
          <div className="profile-screen__section-icon" aria-hidden="true">
            <FileText className="h-4 w-4" />
          </div>
          <div>
            <h2>Custom Instructions</h2>
            <p>{normalizedCustomInstructionsDraft.length}/{data.customInstructionsMaxChars} characters</p>
          </div>
        </div>
        <form
          className="profile-screen__custom-instructions-form"
          onSubmit={(event) => {
            event.preventDefault()
            void handleCustomInstructionsSave()
          }}
        >
          <div className="profile-screen__form-grid">
            <label className="profile-screen__field profile-screen__field--wide">
              <span>Instructions</span>
              <textarea
                value={customInstructionsDraft}
                onChange={(event) => {
                  setCustomInstructionsDraft(event.target.value)
                  if (customInstructionsErrors.length) {
                    setCustomInstructionsErrors([])
                  }
                  if (customInstructionsMessage) {
                    setCustomInstructionsMessage(null)
                  }
                }}
                className="profile-screen__custom-instructions-textarea"
                placeholder="Follow my tone, preferences, and operating style."
              />
              {customInstructionsErrors.map((message) => (
                <em key={message}>{message}</em>
              ))}
            </label>
          </div>
          <div className="profile-screen__actions">
            <button
              type="submit"
              className="profile-screen__button profile-screen__button--primary"
              disabled={savingCustomInstructions || !customInstructionsChanged || customInstructionsOverLimit}
            >
              <Save className="h-4 w-4" aria-hidden="true" />
              {savingCustomInstructions ? 'Saving...' : 'Save Instructions'}
            </button>
            {customInstructionsMessage ? (
              <p className="profile-screen__feedback profile-screen__feedback--success">{customInstructionsMessage}</p>
            ) : null}
          </div>
        </form>
      </section>

      <section className="profile-screen__section">
        <div className="profile-screen__section-header">
          <div className="profile-screen__section-icon" aria-hidden="true">
            <Copy className="h-4 w-4" />
          </div>
          <div>
            <h2>Referral Link</h2>
            <p>Share Gobii with a teammate or partner.</p>
          </div>
        </div>
        <div className="profile-screen__copy-row">
          <input
            ref={referralInputRef}
            value={data.referralLink}
            readOnly
            aria-label="Referral link"
          />
          <button
            type="button"
            className="profile-screen__button profile-screen__button--secondary"
            onClick={() => void handleCopyReferral()}
          >
            <Copy className="h-4 w-4" aria-hidden="true" />
            Copy
          </button>
        </div>
        {copyMessage ? <p className="profile-screen__feedback profile-screen__feedback--success">{copyMessage}</p> : null}
      </section>

      <EmailVerificationSection
        email={data.emailVerification.email}
        isVerified={data.emailVerification.isVerified}
        onVerifiedChange={(verified) => {
          setData((current) => ({
            ...current,
            emailVerification: {
              ...current.emailVerification,
              isVerified: verified,
            },
          }))
        }}
      />

      <PhoneSection
        phone={data.phone}
        onPhoneChange={(phone) => setData((current) => ({ ...current, phone }))}
      />
    </div>
  )
}
