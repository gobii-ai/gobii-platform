import type { FormEvent } from 'react'

import { FolderPlus } from 'lucide-react'
import { SettingsActionButton } from '../agentSettings/SettingsControls'

type CreateFolderFormProps = {
  folderName: string
  isBusy: boolean
  onNameChange: (value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function CreateFolderForm({
  folderName,
  isBusy,
  onNameChange,
  onSubmit,
}: CreateFolderFormProps) {
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={onSubmit}>
      <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded-lg border border-slate-300/70 bg-slate-900/40 px-3 py-2">
        <FolderPlus className="h-4 w-4 text-emerald-300" aria-hidden="true" />
        <input
          type="text"
          name="folderName"
          value={folderName}
          onChange={(event) => onNameChange(event.target.value)}
          autoFocus
          className="flex-1 bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-500"
          placeholder="New folder name"
        />
      </div>
      <SettingsActionButton
        type="submit"
        tone="success"
        disabled={isBusy}
      >
        Create folder
      </SettingsActionButton>
    </form>
  )
}
