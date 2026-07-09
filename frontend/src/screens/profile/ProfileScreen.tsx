import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircle2,
  Copy,
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
  resendEmailVerification,
} from '../../api/agentSetup'
import type { PhoneState } from '../../api/agentSetup'
import { HttpError } from '../../api/http'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { updateUserCustomInstructions, updateUserProfile } from '../../api/userProfile'
import type { UserProfileFormState, UserProfilePayload } from '../../api/userProfile'
import { PhoneNumberInput, type SupportedPhoneRegion } from '../../components/common/PhoneNumberInput'
import { CustomInstructionsSection } from '../../components/settings/CustomInstructionsSection'
import { useUserPhoneVerification } from '../../hooks/useUserPhoneVerification'

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
        pendingPhone={data.pendingPhone ?? null}
        supportedRegions={data.supportedPhoneRegions}
        onPhoneChange={(phone, pendingPhone) => setData((current) => ({ ...current, phone, pendingPhone }))}
      />
    </div>
  )
}
