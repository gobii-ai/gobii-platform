import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, Copy, Mail, Phone, RefreshCcw, Save, ShieldCheck, Trash2, User, XCircle } from 'lucide-react'

import type { PhoneState } from '../../api/agentSetup'
import { HttpError } from '../../api/http'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { updateUserCustomInstructions, updateUserEmail, updateUserProfile } from '../../api/userProfile'
import type { EmailVerificationState, UserProfileFormState, UserProfilePayload } from '../../api/userProfile'
import { PhoneNumberInput, type SupportedPhoneRegion } from '../../components/common/PhoneNumberInput'
// complexity-budget: exclude-start pet
import { PetProfileSection } from '../../components/pets/PetProfileSection'
// complexity-budget: exclude-end pet
import { CustomInstructionsSection } from '../../components/settings/CustomInstructionsSection'
import { useUserPhoneVerification } from '../../hooks/useUserPhoneVerification'

type ProfileScreenProps = {
  initialData: UserProfilePayload
}

type ProfileFieldErrors = Partial<Record<keyof UserProfileFormState | 'profile' | 'email' | 'customInstructions' | 'nonFieldErrors', string[]>>

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

function formatCustomInstructionsErrors(error: unknown): string[] {
  const fieldErrors = extractProfileErrors(error)
  const customError = firstError(fieldErrors, 'customInstructions')
  return [customError || safeErrorMessage(error)]
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

function EmailAddressSection({
  emailVerification,
  onChange,
}: {
  emailVerification: EmailVerificationState
  onChange: (state: EmailVerificationState) => void
}) {
  const [emailDraft, setEmailDraft] = useState('')
  const [busyAction, setBusyAction] = useState<'change' | 'resend' | 'cancel' | null>(null)
  const [feedback, setFeedback] = useState<{ kind: 'success' | 'error'; text: string; field?: boolean } | null>(null)

  async function runAction(action: 'change' | 'resend' | 'cancel') {
    setBusyAction(action)
    setFeedback(null)
    try {
      const result = await updateUserEmail(action, action === 'change' ? emailDraft : undefined)
      onChange(result.emailVerification)
      if (action === 'change') setEmailDraft('')
      setFeedback({
        kind: 'success',
        text: action === 'cancel' ? 'Email change canceled.' : `Check ${action === 'change' ? 'the new inbox' : 'your inbox'} for the verification link.`,
      })
    } catch (error) {
      const fieldErrors = extractProfileErrors(error)
      const fieldError = firstError(fieldErrors, 'email')
      setFeedback({
        kind: 'error',
        text: fieldError || firstError(fieldErrors, 'nonFieldErrors') || safeErrorMessage(error),
        field: Boolean(fieldError),
      })
    } finally {
      setBusyAction(null)
    }
  }

  const pendingEmail = emailVerification.pendingEmail

  return (
    <section className="profile-screen__section">
      <div className="profile-screen__section-header">
        <div className="profile-screen__section-icon" aria-hidden="true">
          <Mail className="h-4 w-4" />
        </div>
        <div>
          <h2>Email Address</h2>
          <p>Used to sign in and receive account notifications.</p>
        </div>
      </div>
      <div className="profile-screen__status-row">
        <div>
          <p className="profile-screen__muted">Current email</p>
          <p>{emailVerification.email || 'No email address on file'}</p>
        </div>
        <span className={`profile-screen__status profile-screen__status--${emailVerification.isVerified ? 'success' : 'warning'}`}>
          {emailVerification.isVerified
            ? <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            : <XCircle className="h-4 w-4" aria-hidden="true" />}
          {emailVerification.isVerified ? 'Verified' : 'Unverified'}
        </span>
        {!emailVerification.isVerified && !pendingEmail && emailVerification.email ? (
          <button type="button" className="profile-screen__button profile-screen__button--secondary"
            onClick={() => void runAction('resend')} disabled={busyAction !== null}>
            <RefreshCcw className="h-4 w-4" aria-hidden="true" />
            {busyAction === 'resend' ? 'Sending...' : 'Resend Verification'}
          </button>
        ) : null}
      </div>

      {pendingEmail ? (
        <div className="profile-screen__verify-form">
          <p><strong>Changing to {pendingEmail}</strong></p>
          <p className="profile-screen__muted">
            Your current email remains active until this address is verified.
          </p>
          <div className="profile-screen__button-row">
            <button type="button" className="profile-screen__button profile-screen__button--secondary"
              onClick={() => void runAction('resend')} disabled={busyAction !== null}>
              <RefreshCcw className="h-4 w-4" aria-hidden="true" />
              {busyAction === 'resend' ? 'Sending...' : 'Resend Verification'}
            </button>
            <button type="button" className="profile-screen__button profile-screen__button--secondary"
              onClick={() => void runAction('cancel')} disabled={busyAction !== null}>
              {busyAction === 'cancel' ? 'Canceling...' : 'Cancel Change'}
            </button>
          </div>
        </div>
      ) : (
        <form className="profile-screen__inline-form" onSubmit={(event) => {
            event.preventDefault()
            if (emailDraft.trim() && !busyAction) void runAction('change')
          }}>
          <label className="profile-screen__field">
            <span>New Email</span>
            <input type="email" value={emailDraft} onChange={(event) => {
                setEmailDraft(event.target.value)
                setFeedback(null)
              }}
              autoComplete="email"
              placeholder="name@example.com"
              disabled={busyAction !== null} />
            {feedback?.kind === 'error' && feedback.field ? <em>{feedback.text}</em> : null}
          </label>
          <button type="submit" className="profile-screen__button profile-screen__button--primary"
            disabled={busyAction !== null || !emailDraft.trim()}>
            <Mail className="h-4 w-4" aria-hidden="true" />
            {busyAction === 'change' ? 'Sending...' : 'Change Email'}
          </button>
        </form>
      )}
      {feedback && !feedback.field ? (
        <p className={`profile-screen__feedback profile-screen__feedback--${feedback.kind}`}>{feedback.text}</p>
      ) : null}
    </section>
  )
}

function PhoneSection({
  phone,
  pendingPhone,
  supportedRegions,
  onPhoneChange,
}: {
  phone: PhoneState | null
  pendingPhone: PhoneState | null
  supportedRegions: SupportedPhoneRegion[]
  onPhoneChange: (phone: PhoneState | null, pendingPhone: PhoneState | null) => void
}) {
  const phoneVerification = useUserPhoneVerification({
    phone,
    pendingPhone,
    supportedRegions,
    onPhoneChange,
  })
  const verifiedAt = formatDateTime(phone?.verifiedAt ?? null)

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

      {phoneVerification.showAddForm ? (
        <form
          className="profile-screen__inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            phoneVerification.addPhone()
          }}
        >
          <div className="profile-screen__field profile-screen__field--phone">
            <label htmlFor="profile-phone-number-input">SMS Number</label>
            <PhoneNumberInput
              id="profile-phone-number-input"
              className="profile-screen__phone-input"
              inputClassName="profile-screen__phone-tel"
              selectClassName="profile-screen__phone-country"
              value={phoneVerification.phoneInput}
              region={phoneVerification.phoneRegion}
              supportedRegions={supportedRegions}
              onValueChange={phoneVerification.setPhoneInput}
              onRegionChange={phoneVerification.setPhoneRegion}
              disabled={phoneVerification.busyAction === 'add'}
            />
          </div>
          <button
            type="submit"
            className="profile-screen__button profile-screen__button--primary"
            disabled={phoneVerification.busyAction === 'add'}
          >
            <Phone className="h-4 w-4" aria-hidden="true" />
            {phoneVerification.busyAction === 'add' ? 'Sending...' : 'Add Phone'}
          </button>
        </form>
      ) : phone ? (
        <div className="profile-screen__phone-current">
          <div>
            <p className="profile-screen__phone-number">{phoneVerification.phoneDisplay}</p>
            {phone.isVerified ? (
              <p className="profile-screen__muted">Verified{verifiedAt ? ` ${verifiedAt}` : ''}</p>
            ) : (
              <p className="profile-screen__muted">Verification pending</p>
            )}
          </div>
          <button
            type="button"
            className="profile-screen__button profile-screen__button--secondary"
            onClick={phoneVerification.startReplacingPhone}
            disabled={phoneVerification.busyAction !== null || Boolean(pendingPhone)}
          >
            Replace
          </button>
          <button
            type="button"
            className="profile-screen__icon-button profile-screen__icon-button--danger"
            onClick={phoneVerification.deletePhone}
            disabled={phoneVerification.busyAction === 'delete'}
            aria-label="Remove phone number"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      ) : null}

      {pendingPhone ? (
        <form
          className="profile-screen__verify-form"
          onSubmit={(event) => {
            event.preventDefault()
            phoneVerification.verifyPhone()
          }}
        >
          {phoneVerification.pendingPhoneDisplay ? (
            <p className="profile-screen__muted">Verifying {phoneVerification.pendingPhoneDisplay}</p>
          ) : null}
          <label className="profile-screen__field">
            <span>Verification Code</span>
            <input
              value={phoneVerification.verificationCode}
              onChange={(event) => phoneVerification.setVerificationCode(event.target.value)}
              placeholder="123456"
              inputMode="numeric"
              autoComplete="one-time-code"
            />
          </label>
          <div className="profile-screen__button-row">
            <button
              type="submit"
              className="profile-screen__button profile-screen__button--primary"
              disabled={phoneVerification.busyAction === 'verify'}
            >
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              {phoneVerification.busyAction === 'verify' ? 'Verifying...' : 'Verify'}
            </button>
            <button
              type="button"
              className="profile-screen__button profile-screen__button--secondary"
              onClick={phoneVerification.resendPhone}
              disabled={phoneVerification.busyAction === 'resend' || phoneVerification.resendDisabled}
            >
              <RefreshCcw className="h-4 w-4" aria-hidden="true" />
              {phoneVerification.resendDisabled ? `Resend in ${phoneVerification.pendingCooldown}s` : 'Resend'}
            </button>
            <button
              type="button"
              className="profile-screen__button profile-screen__button--secondary"
              onClick={phoneVerification.cancelPhoneVerification}
              disabled={phoneVerification.busyAction === 'cancel' || phoneVerification.pendingCooldown > 0}
            >
              {phoneVerification.pendingCooldown > 0 ? `Cancel in ${phoneVerification.pendingCooldown}s` : 'Cancel'}
            </button>
          </div>
        </form>
      ) : null}

      {phoneVerification.message ? (
        <p className="profile-screen__feedback profile-screen__feedback--success">{phoneVerification.message}</p>
      ) : null}
      {phoneVerification.error ? (
        <p className="profile-screen__feedback profile-screen__feedback--error">{phoneVerification.error}</p>
      ) : null}
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
  const [copyMessage, setCopyMessage] = useState<string | null>(null)
  const referralInputRef = useRef<HTMLInputElement | null>(null)
  const isDirty = useMemo(() => (
    draft.firstName !== data.profile.firstName
    || draft.lastName !== data.profile.lastName
    || draft.timezone !== data.profile.timezone
  ), [data.profile, draft])

  useEffect(() => {
    setData(initialData)
    setDraft(initialData.profile)
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

  const handleCustomInstructionsSave = useCallback(async (normalizedInstructions: string) => {
    const nextData = await updateUserCustomInstructions(normalizedInstructions)
    setData(nextData)
    return nextData.customInstructions
  }, [])

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

      {/* complexity-budget: exclude-start pet */}
      <PetProfileSection />
      {/* complexity-budget: exclude-end pet */}

      <CustomInstructionsSection
        value={data.customInstructions}
        maxChars={data.customInstructionsMaxChars}
        placeholder="Follow my tone, preferences, and operating style."
        onSave={handleCustomInstructionsSave}
        formatErrorMessages={formatCustomInstructionsErrors}
      />

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

      <EmailAddressSection
        emailVerification={data.emailVerification}
        onChange={(emailVerification) => {
          setData((current) => ({ ...current, emailVerification }))
        }}
      />

      <PhoneSection
        phone={data.phone}
        pendingPhone={data.pendingPhone ?? null}
        supportedRegions={data.supportedPhoneRegions}
        onPhoneChange={(phone, pendingPhone) => setData((current) => ({ ...current, phone, pendingPhone }))}
      />
    </div>
  )
}
