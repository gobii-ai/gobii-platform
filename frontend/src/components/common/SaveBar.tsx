import { Check, Info, XCircle } from 'lucide-react'

export type SaveBarProps = {
  visible: boolean
  onCancel: () => void
  onSave: () => Promise<void> | void
  busy?: boolean
  error?: string | null
  id?: string
}

export function SaveBar({ visible, onCancel, onSave, busy, error, id }: SaveBarProps) {
  if (!visible) {
    return null
  }

  return (
    <div id={id} className="fixed inset-x-0 bottom-0 z-40 pointer-events-none">
      <div className="pointer-events-auto mx-auto w-full max-w-5xl px-4 pb-4">
        <div className="flex flex-col gap-3 rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-[0_8px_30px_rgba(15,23,42,0.25)] sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col gap-1 text-sm text-gray-700">
            <div className="flex items-center gap-2">
              <Info className="h-4 w-4 text-blue-600" aria-hidden="true" />
              <span>You have unsaved changes</span>
            </div>
            {error && (
              <div className="flex items-center gap-2 text-xs text-red-600">
                <XCircle className="h-4 w-4" aria-hidden="true" />
                <span>{error}</span>
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg border border-transparent bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-60"
            >
              <Check className="h-4 w-4" aria-hidden="true" />
              {busy ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

