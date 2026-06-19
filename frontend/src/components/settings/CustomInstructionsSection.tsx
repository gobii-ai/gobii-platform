import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FileText, Save } from 'lucide-react'

import { safeErrorMessage } from '../../api/safeErrorMessage'

type CustomInstructionsSectionProps = {
  value: string
  maxChars: number
  canEdit?: boolean
  placeholder: string
  successMessage?: string
  errorFallback?: string
  onSave: (normalizedInstructions: string) => Promise<string | void>
  formatErrorMessages?: (error: unknown) => string[]
}

function normalizeInstructions(value: string): string {
  return value.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim()
}

function defaultFormatErrorMessages(error: unknown, fallback: string): string[] {
  const message = safeErrorMessage(error)
  return [message || fallback]
}

export function CustomInstructionsSection({
  value,
  maxChars,
  canEdit = true,
  placeholder,
  successMessage = 'Custom instructions saved.',
  errorFallback = 'Unable to update custom instructions.',
  onSave,
  formatErrorMessages,
}: CustomInstructionsSectionProps) {
  const [draft, setDraft] = useState(value)
  const [message, setMessage] = useState<string | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const savedValueRef = useRef<string | null>(null)
  const normalizedDraft = useMemo(() => normalizeInstructions(draft), [draft])
  const changed = normalizedDraft !== value
  const overLimit = normalizedDraft.length > maxChars

  useEffect(() => {
    setDraft(value)
    setErrors([])
    if (savedValueRef.current === value) {
      savedValueRef.current = null
    } else {
      savedValueRef.current = null
      setMessage(null)
    }
  }, [value])

  const handleSave = useCallback(async () => {
    if (normalizedDraft.length > maxChars) {
      setErrors([`Custom instructions must be ${maxChars} characters or fewer.`])
      return
    }

    setSaving(true)
    setErrors([])
    setMessage(null)
    try {
      const savedInstructions = await onSave(normalizedDraft)
      const nextDraft = savedInstructions ?? normalizedDraft
      savedValueRef.current = nextDraft
      setDraft(nextDraft)
      setMessage(successMessage)
    } catch (err) {
      setErrors(formatErrorMessages ? formatErrorMessages(err) : defaultFormatErrorMessages(err, errorFallback))
    } finally {
      setSaving(false)
    }
  }, [errorFallback, formatErrorMessages, maxChars, normalizedDraft, onSave, successMessage])

  return (
    <section className="profile-screen__section">
      <div className="profile-screen__section-header">
        <div className="profile-screen__section-icon" aria-hidden="true">
          <FileText className="h-4 w-4" />
        </div>
        <div>
          <h2>Custom Instructions</h2>
          <p>{normalizedDraft.length}/{maxChars} characters</p>
        </div>
      </div>
      <form
        className="profile-screen__custom-instructions-form"
        onSubmit={(event) => {
          event.preventDefault()
          void handleSave()
        }}
      >
        <div className="profile-screen__form-grid">
          <label className="profile-screen__field profile-screen__field--wide">
            <span>Instructions</span>
            <textarea
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value)
                if (errors.length) {
                  setErrors([])
                }
                if (message) {
                  setMessage(null)
                }
              }}
              disabled={!canEdit || saving}
              className="profile-screen__custom-instructions-textarea"
              placeholder={placeholder}
            />
            {errors.map((errorMessage) => (
              <em key={errorMessage}>{errorMessage}</em>
            ))}
          </label>
        </div>
        <div className="profile-screen__actions">
          {canEdit ? (
            <button
              type="submit"
              className="profile-screen__button profile-screen__button--primary"
              disabled={saving || !changed || overLimit}
            >
              <Save className="h-4 w-4" aria-hidden="true" />
              {saving ? 'Saving...' : 'Save Instructions'}
            </button>
          ) : null}
          {message ? (
            <p className="profile-screen__feedback profile-screen__feedback--success">{message}</p>
          ) : null}
        </div>
      </form>
    </section>
  )
}
