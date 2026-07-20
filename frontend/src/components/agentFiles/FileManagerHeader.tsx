import type { KeyboardEvent } from 'react'

import { FolderPlus, RefreshCw, Trash2, UploadCloud } from 'lucide-react'
import { EmbeddedAgentShellBackButton } from '../agentChat/EmbeddedAgentShellBackButton'
import { SettingsBanner } from '../agentSettings/SettingsBanner'
import { getSettingsActionButtonClassName } from '../agentSettings/SettingsControls'

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

  const uploadButtonClassName = getSettingsActionButtonClassName({ tone: 'primary', responsive: true, className: isBusy ? 'cursor-not-allowed opacity-60' : 'cursor-pointer' })
  const folderButtonClassName = getSettingsActionButtonClassName({ tone: 'success', responsive: true })
  const deleteButtonClassName = getSettingsActionButtonClassName({ tone: 'danger', responsive: true })
  const refreshButtonClassName = getSettingsActionButtonClassName({ responsive: true })

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
