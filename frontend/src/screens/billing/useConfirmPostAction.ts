import { useCallback, useState } from 'react'

import { jsonRequest } from '../../api/http'
import { safeErrorMessage } from '../../api/safeErrorMessage'

type ConfirmPostActionOptions = {
  url?: string
  defaultErrorMessage: string
}

export function useConfirmPostAction({ url, defaultErrorMessage }: ConfirmPostActionOptions) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const openDialog = useCallback(() => {
    setError(null)
    setOpen(true)
  }, [])

  const closeDialog = useCallback(() => {
    if (busy) return
    setOpen(false)
  }, [busy])

  const confirm = useCallback(async () => {
    if (busy) return
    if (!url) return
    setBusy(true)
    setError(null)
    try {
      const result = await jsonRequest<{ success: boolean; error?: string }>(url, {
        method: 'POST',
        includeCsrf: true,
      })
      if (!result?.success) {
        setError(result?.error ?? defaultErrorMessage)
        return
      }
      window.location.reload()
    } catch (e) {
      setError(safeErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }, [busy, defaultErrorMessage, url])

  return {
    open,
    busy,
    error,
    openDialog,
    closeDialog,
    confirm,
  }
}

