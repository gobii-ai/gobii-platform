import type { KeyboardEvent } from 'react'

import { ArrowLeft, FolderPlus, RefreshCw, Trash2, UploadCloud } from 'lucide-react'

type FileManagerHeaderProps = {
  agentName: string
  agentDetailUrl: string
  uploadInputId: string
  isBusy: boolean
  isCreatingFolder: boolean
  selectedRows: number
  isRefreshing: boolean
  onUploadRequest: () => void
  onTriggerUploadInput: () => void
  onToggleCreateFolder: () => void
  onBulkDelete: () => void
  onRefresh: () => void
}

export function FileManagerHeader({
  agentName,
  agentDetailUrl,
  uploadInputId,
  isBusy,
  isCreatingFolder,
  selectedRows,
  isRefreshing,
  onUploadRequest,
  onTriggerUploadInput,
  onToggleCreateFolder,
  onBulkDelete,
  onRefresh,
}: FileManagerHeaderProps) {
  const handleUploadKeyDown = (event: KeyboardEvent<HTMLLabelElement>) => {
    if (isBusy) {
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onUploadRequest()
      onTriggerUploadInput()
    }
  }

  return (
    <div className="flex flex-col gap-4 border-b border-slate-200/70 px-6 py-5 lg:flex-row lg:items-center lg:justify-between">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Agent Files</h1>
        <p className="mt-1 text-sm text-slate-600">Browse and manage files for {agentName}.</p>
        <a href={agentDetailUrl} className="mt-3 inline-flex items-center gap-2 text-sm text-blue-700 hover:text-blue-900">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to Agent Settings
        </a>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor={uploadInputId}
          role="button"
          tabIndex={isBusy ? -1 : 0}
          aria-disabled={isBusy}
          className={`inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-semibold text-blue-700 transition ${isBusy ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-blue-100'}`}
          onPointerDown={(event) => {
            if (isBusy) {
              event.preventDefault()
              return
            }
            onUploadRequest()
          }}
          onKeyDown={handleUploadKeyDown}
        >
          <UploadCloud className="h-4 w-4" aria-hidden="true" />
          Upload Files
        </label>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-60"
          onClick={onToggleCreateFolder}
          disabled={isBusy}
        >
          <FolderPlus className="h-4 w-4" aria-hidden="true" />
          {isCreatingFolder ? 'Cancel' : 'New Folder'}
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-700 transition hover:bg-rose-100 disabled:opacity-60"
          onClick={onBulkDelete}
          disabled={isBusy || selectedRows === 0}
        >
          <Trash2 className="h-4 w-4" aria-hidden="true" />
          Delete Selected
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-blue-50 disabled:opacity-60"
          onClick={onRefresh}
          disabled={isRefreshing}
        >
          <RefreshCw className={`h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
          Refresh
        </button>
      </div>
    </div>
  )
}
