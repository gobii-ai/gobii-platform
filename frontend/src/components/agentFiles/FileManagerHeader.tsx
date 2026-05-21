import type { KeyboardEvent } from 'react'

import { FolderPlus, RefreshCw, Trash2, UploadCloud } from 'lucide-react'
import { EmbeddedAgentShellBackButton } from '../agentChat/EmbeddedAgentShellBackButton'
import { SettingsBanner } from '../agentSettings/SettingsBanner'

type FileManagerHeaderProps = {
  agentName: string
  onBack?: () => void
  canManage: boolean
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
  onBack,
  canManage,
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

  const uploadButtonClassName = `inline-flex w-full items-center justify-center gap-2 rounded-lg border border-blue-300/40 bg-blue-950/20 px-3 py-2 text-sm font-semibold text-blue-100 transition sm:w-auto ${isBusy ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:border-blue-200 hover:bg-blue-900/30'}`
  const folderButtonClassName = 'inline-flex w-full items-center justify-center gap-2 rounded-lg border border-emerald-300/40 bg-emerald-950/20 px-3 py-2 text-sm font-semibold text-emerald-100 transition hover:border-emerald-200 hover:bg-emerald-900/30 disabled:opacity-60 sm:w-auto'
  const deleteButtonClassName = 'inline-flex w-full items-center justify-center gap-2 rounded-lg border border-rose-300/40 bg-rose-950/20 px-3 py-2 text-sm font-semibold text-rose-100 transition hover:border-rose-200 hover:bg-rose-900/30 disabled:opacity-60 sm:w-auto'
  const refreshButtonClassName = 'inline-flex w-full items-center justify-center gap-2 rounded-lg border border-slate-300/70 bg-slate-900/40 px-3 py-2 text-sm font-medium text-slate-100 transition hover:border-slate-200 hover:bg-slate-900/60 disabled:opacity-60 sm:w-auto'

  return (
    <SettingsBanner
      variant="embedded"
      leading={<EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to settings" />}
      eyebrow="File manager"
      title={agentName}
      actions={(
        <>
          <label
            htmlFor={uploadInputId}
            role="button"
            tabIndex={isBusy ? -1 : 0}
            aria-disabled={isBusy}
            className={uploadButtonClassName}
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
          {canManage ? (
            <button
              type="button"
              className={folderButtonClassName}
              onClick={onToggleCreateFolder}
              disabled={isBusy}
            >
              <FolderPlus className="h-4 w-4" aria-hidden="true" />
              {isCreatingFolder ? 'Cancel' : 'New Folder'}
            </button>
          ) : null}
          {canManage ? (
            <button
              type="button"
              className={deleteButtonClassName}
              onClick={onBulkDelete}
              disabled={isBusy || selectedRows === 0}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Delete Selected
            </button>
          ) : null}
          <button
            type="button"
            className={refreshButtonClassName}
            onClick={onRefresh}
            disabled={isRefreshing}
          >
            <RefreshCw className={`h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
            Refresh
          </button>
        </>
      )}
    />
  )
}
