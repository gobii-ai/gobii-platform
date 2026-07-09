import { useEffect, useState } from 'react'

import { addUserPhone, cancelUserPhoneVerification, deleteUserPhone, resendUserPhone, verifyUserPhone, type PhoneResponse, type PhoneState } from '../api/agentSetup'
import { safeErrorMessage } from '../api/safeErrorMessage'
import { DEFAULT_PHONE_REGION, formatPhoneE164, formatPhoneNational, normalizePhoneRegion, type SupportedPhoneRegion } from '../components/common/PhoneNumberInput'

export type UserPhoneVerificationAction = 'add' | 'verify' | 'resend' | 'cancel' | 'delete'

type UseUserPhoneVerificationOptions = {
  phone: PhoneState | null
  pendingPhone: PhoneState | null
  supportedRegions: SupportedPhoneRegion[]
  describeError?: (error: unknown) => string
  onPhoneChange?: (phone: PhoneState | null, pendingPhone: PhoneState | null) => void
  onAddSuccess?: () => void
  onVerifySuccess?: () => void
}

const SUCCESS_MESSAGES: Record<UserPhoneVerificationAction, string> = {
  add: 'Verification code sent.',
  verify: 'Phone number verified.',
  resend: 'Verification code resent.',
  cancel: 'Phone verification canceled.',
  delete: 'Phone number removed.',
}

export function useUserPhoneVerification({
  phone,
  pendingPhone,
  supportedRegions,
  describeError = safeErrorMessage,
  onPhoneChange,
  onAddSuccess,
  onVerifySuccess,
}: UseUserPhoneVerificationOptions) {
  const [verifiedPhone, setVerifiedPhone] = useState<PhoneState | null>(phone)
  const [pendingUserPhone, setPendingUserPhone] = useState<PhoneState | null>(pendingPhone)
  const [phoneInput, setPhoneInput] = useState('')
  const [phoneRegion, setPhoneRegion] = useState(() => normalizePhoneRegion(DEFAULT_PHONE_REGION, supportedRegions))
  const [replacingPhone, setReplacingPhone] = useState(false)
  const [verificationCode, setVerificationCode] = useState('')
  const [busyAction, setBusyAction] = useState<UserPhoneVerificationAction | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pendingCooldown, setPendingCooldown] = useState(pendingPhone?.cooldownRemaining ?? 0)

  useEffect(() => {
    setVerifiedPhone(phone)
    setPendingUserPhone(pendingPhone)
    setReplacingPhone(false)
  }, [pendingPhone, phone])

  useEffect(() => {
    setPhoneRegion((current) => normalizePhoneRegion(current, supportedRegions))
  }, [supportedRegions])

  useEffect(() => {
    setPendingCooldown(pendingUserPhone?.cooldownRemaining ?? 0)
  }, [pendingUserPhone?.cooldownRemaining])

  useEffect(() => {
    if (pendingCooldown <= 0) {
      return undefined
    }
    const timer = window.setTimeout(() => setPendingCooldown((current) => Math.max(current - 1, 0)), 1000)
    return () => window.clearTimeout(timer)
  }, [pendingCooldown])

  function applyPhoneResponse(response: PhoneResponse) {
    const nextPhone = response.phone ?? null
    const nextPendingPhone = response.pendingPhone ?? null
    setVerifiedPhone(nextPhone)
    setPendingUserPhone(nextPendingPhone)
    onPhoneChange?.(nextPhone, nextPendingPhone)
  }

  async function runPhoneAction(
    action: UserPhoneVerificationAction,
    fn: () => Promise<PhoneResponse>,
    afterSuccess?: () => void,
  ) {
    setBusyAction(action)
    setMessage(null)
    setError(null)
    try {
      applyPhoneResponse(await fn())
      setMessage(SUCCESS_MESSAGES[action])
      afterSuccess?.()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusyAction(null)
    }
  }

  function clearVerificationForm() {
    setVerificationCode('')
    setReplacingPhone(false)
  }

  return {
    verifiedPhone,
    pendingPhone: pendingUserPhone,
    phoneInput,
    setPhoneInput,
    phoneRegion,
    setPhoneRegion,
    replacingPhone,
    verificationCode,
    setVerificationCode,
    busyAction,
    message,
    error,
    pendingCooldown,
    resendDisabled: pendingCooldown > 0,
    showAddForm: !pendingUserPhone && (!verifiedPhone || replacingPhone),
    phoneDisplay: verifiedPhone?.number ? formatPhoneNational(verifiedPhone.number, phoneRegion, supportedRegions) : '',
    pendingPhoneDisplay: pendingUserPhone?.number ? formatPhoneNational(pendingUserPhone.number, phoneRegion, supportedRegions) : '',
    applyPhoneResponse,
    addPhone() {
      const trimmed = phoneInput.trim()
      if (!trimmed) {
        setError('Phone number is required.')
        return
      }
      void runPhoneAction('add', () => addUserPhone(formatPhoneE164(trimmed, phoneRegion, supportedRegions)), () => {
        setPhoneInput('')
        setReplacingPhone(false)
        onAddSuccess?.()
      })
    },
    verifyPhone() {
      const code = verificationCode.trim()
      if (!code) {
        setError('Verification code is required.')
        return
      }
      void runPhoneAction('verify', () => verifyUserPhone(code), () => {
        clearVerificationForm()
        onVerifySuccess?.()
      })
    },
    resendPhone() {
      void runPhoneAction('resend', resendUserPhone)
    },
    cancelPhoneVerification() {
      if (pendingCooldown === 0) {
        void runPhoneAction('cancel', cancelUserPhoneVerification, clearVerificationForm)
      }
    },
    deletePhone() {
      void runPhoneAction('delete', deleteUserPhone, clearVerificationForm)
    },
    startReplacingPhone() {
      setReplacingPhone(true)
      setPhoneInput('')
      setError(null)
      setMessage(null)
    },
  }
}
