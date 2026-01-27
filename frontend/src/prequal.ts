import { HttpError } from './api/http'
import { submitPrequalify, type PrequalifyPayload } from './api/prequalify'

type MessageKind = 'success' | 'error'

type ErrorPayload = {
  message?: string
  errors?: unknown
}

const form = document.querySelector<HTMLFormElement>('[data-prequal-form]')

if (form) {
  const responseEl = form.querySelector<HTMLElement>('[data-prequal-response]')
  const submitButton = form.querySelector<HTMLButtonElement>('[data-prequal-submit]')
  const submitLabel = submitButton?.textContent?.trim() || 'Submit'
  let isSubmitting = false
  let isSubmitted = false

  const setBusy = (busy: boolean) => {
    isSubmitting = busy
    if (submitButton) {
      if (busy) {
        submitButton.disabled = true
        submitButton.textContent = 'Submitting...'
      } else if (!isSubmitted) {
        submitButton.disabled = false
        submitButton.textContent = submitLabel
      }
    }
  }

  const setFormEnabled = (enabled: boolean) => {
    const fields = form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
      'input, select, textarea',
    )
    fields.forEach((field) => {
      field.disabled = !enabled
    })
  }

  const renderMessage = (kind: MessageKind, message: string, items: string[] = []) => {
    if (!responseEl) return
    const escapeHtml = (value: string) =>
      value.replace(/[&<>"']/g, (char) => {
        const map: Record<string, string> = {
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        }
        return map[char] || char
      })
    const tone =
      kind === 'success'
        ? 'rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700'
        : 'rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700'
    const title = kind === 'success' ? 'Request received' : 'Please review the details'
    const safeMessage = escapeHtml(message)
    const safeItems = items.map((item) => escapeHtml(item))
    const listItems = items.length
      ? `<ul class="mt-2 list-disc list-inside">${safeItems.map((item) => `<li>${item}</li>`).join('')}</ul>`
      : ''
    responseEl.innerHTML = `
      <div class="${tone}" role="${kind === 'success' ? 'status' : 'alert'}">
        <p class="font-semibold">${title}</p>
        <p class="mt-1">${safeMessage}</p>
        ${listItems}
      </div>
    `
  }

  const clearMessage = () => {
    if (responseEl) {
      responseEl.innerHTML = ''
    }
  }

  const collectPayload = (): PrequalifyPayload => {
    const payload: PrequalifyPayload = {}
    const data = new FormData(form)
    data.forEach((value, key) => {
      if (typeof value === 'string') {
        payload[key] = key === 'cf-turnstile-response' ? value : value.trim()
      }
    })
    return payload
  }

  const extractErrorItems = (errors: unknown): string[] => {
    if (!errors) return []
    if (Array.isArray(errors)) {
      return errors.filter((item): item is string => typeof item === 'string')
    }
    if (typeof errors === 'object') {
      const items: string[] = []
      Object.entries(errors as Record<string, unknown>).forEach(([field, value]) => {
        if (Array.isArray(value)) {
          value.forEach((entry) => {
            if (typeof entry === 'string') {
              items.push(`${field}: ${entry}`)
            }
          })
        } else if (typeof value === 'string') {
          items.push(`${field}: ${value}`)
        }
      })
      return items
    }
    if (typeof errors === 'string') return [errors]
    return []
  }

  const parseError = (error: unknown): { message: string; items: string[] } => {
    const fallback = 'Something went wrong. Please try again.'
    if (error instanceof HttpError) {
      const body = error.body as ErrorPayload | string | null
      if (body && typeof body === 'object') {
        const items = extractErrorItems(body.errors)
        const message = typeof body.message === 'string' ? body.message : fallback
        return { message, items }
      }
      if (typeof body === 'string' && body.trim()) {
        return { message: body, items: [] }
      }
      return { message: fallback, items: [] }
    }
    if (error instanceof Error && error.message) {
      return { message: error.message, items: [] }
    }
    return { message: fallback, items: [] }
  }

  const resetTurnstile = () => {
    const widget = (window as Window & { turnstile?: { reset: () => void } }).turnstile
    if (widget && typeof widget.reset === 'function') {
      widget.reset()
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault()
    if (isSubmitting || isSubmitted) return

    clearMessage()
    setBusy(true)

    try {
      const payload = collectPayload()
      const submitUrl = form.getAttribute('action') || window.location.href
      const response = await submitPrequalify(submitUrl, payload)

      if (!response.ok) {
        const items = extractErrorItems(response.errors)
        renderMessage('error', response.message || 'Please check the form and try again.', items)
        resetTurnstile()
        return
      }

      renderMessage('success', response.message || 'Thanks. We will be in touch soon.')
      setFormEnabled(false)
      if (submitButton) {
        submitButton.disabled = true
        submitButton.textContent = 'Submitted'
      }
      isSubmitted = true
    } catch (error) {
      const parsed = parseError(error)
      renderMessage('error', parsed.message, parsed.items)
      resetTurnstile()
    } finally {
      setBusy(false)
    }
  })
}
