import type { KeyboardEvent } from 'react'

import { ArrowLeft, FolderPlus, RefreshCw, Trash2, UploadCloud } from 'lucide-react'
import { EmbeddedAgentShellBackButton } from '../agentChat/EmbeddedAgentShellBackButton'
import { SettingsBanner } from '../agentSettings/SettingsBanner'

type FileManagerHeaderProps = {
  agentName: string
  backLink: {
    url: string
    label: string
  }
  variant?: 'standalone' | 'embedded'
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
  backLink,
  variant = 'standalone',
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

  const isEmbedded = variant === 'embedded'
  const uploadButtonClassName = isEmbedded
    ? `inline-flex items-center gap-2 rounded-lg border border-blue-300/40 bg-blue-950/20 px-3 py-2 text-sm font-semibold text-blue-100 transition ${isBusy ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:border-blue-200 hover:bg-blue-900/30'}`
    : `inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-semibold text-blue-700 transition ${isBusy ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-blue-100'}`
  const folderButtonClassName = isEmbedded
    ? 'inline-flex items-center gap-2 rounded-lg border border-emerald-300/40 bg-emerald-950/20 px-3 py-2 text-sm font-semibold text-emerald-100 transition hover:border-emerald-200 hover:bg-emerald-900/30 disabled:opacity-60'
    : 'inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-60'
  const deleteButtonClassName = isEmbedded
    ? 'inline-flex items-center gap-2 rounded-lg border border-rose-300/40 bg-rose-950/20 px-3 py-2 text-sm font-semibold text-rose-100 transition hover:border-rose-200 hover:bg-rose-900/30 disabled:opacity-60'
    : 'inline-flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-700 transition hover:bg-rose-100 disabled:opacity-60'
  const refreshButtonClassName = isEmbedded
    ? 'inline-flex items-center gap-2 rounded-lg border border-slate-300/70 bg-slate-900/40 px-3 py-2 text-sm font-medium text-slate-100 transition hover:border-slate-200 hover:bg-slate-900/60 disabled:opacity-60'
    : 'inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-blue-50 disabled:opacity-60'

  return (
    <SettingsBanner
      variant={isEmbedded ? 'embedded' : 'standalone'}
      leading={isEmbedded ? <EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to settings" /> : undefined}
      eyebrow={isEmbedded ? 'File manager' : undefined}
      title={isEmbedded ? agentName : 'Agent Files'}
      subtitle={isEmbedded ? undefined : `Browse and manage files for ${agentName}.`}
      supportingContent={!isEmbedded ? (
        <a href={backLink.url} className="group inline-flex items-center gap-2 text-sm text-blue-700 transition-colors hover:text-blue-900">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          {backLink.label}
        </a>
      ) : undefined}
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
