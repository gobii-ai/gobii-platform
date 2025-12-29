import type { FormEvent } from 'react'

import { FolderPlus } from 'lucide-react'

type CreateFolderFormProps = {
  folderName: string
  isBusy: boolean
  onNameChange: (value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function CreateFolderForm({ folderName, isBusy, onNameChange, onSubmit }: CreateFolderFormProps) {
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={onSubmit}>
      <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
        <FolderPlus className="h-4 w-4 text-emerald-600" aria-hidden="true" />
        <input
          type="text"
          name="folderName"
          value={folderName}
          onChange={(event) => onNameChange(event.target.value)}
          autoFocus
          className="flex-1 bg-white text-sm text-slate-700 outline-none"
          placeholder="New folder name"
        />
      </div>
      <button
        type="submit"
        className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-60"
        disabled={isBusy}
      >
        Create folder
      </button>
    </form>
  )
}
