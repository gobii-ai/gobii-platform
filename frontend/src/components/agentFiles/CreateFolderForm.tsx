import type { FormEvent } from 'react'

import { FolderPlus } from 'lucide-react'

type CreateFolderFormProps = {
  folderName: string
  isBusy: boolean
  embedded?: boolean
  onNameChange: (value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function CreateFolderForm({
  folderName,
  isBusy,
  embedded = false,
  onNameChange,
  onSubmit,
}: CreateFolderFormProps) {
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={onSubmit}>
      <div className={embedded ? 'flex min-w-[220px] flex-1 items-center gap-2 rounded-lg border border-slate-300/70 bg-slate-900/40 px-3 py-2' : 'flex min-w-[220px] flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2'}>
        <FolderPlus className={embedded ? 'h-4 w-4 text-emerald-300' : 'h-4 w-4 text-emerald-600'} aria-hidden="true" />
        <input
          type="text"
          name="folderName"
          value={folderName}
          onChange={(event) => onNameChange(event.target.value)}
          autoFocus
          className={embedded ? 'flex-1 bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-500' : 'flex-1 bg-white text-sm text-slate-700 outline-none'}
          placeholder="New folder name"
        />
      </div>
      <button
        type="submit"
        className={embedded ? 'inline-flex items-center gap-2 rounded-lg border border-emerald-300/40 bg-emerald-950/20 px-3 py-2 text-sm font-semibold text-emerald-100 transition hover:border-emerald-200 hover:bg-emerald-900/30 disabled:opacity-60' : 'inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-60'}
        disabled={isBusy}
      >
        Create folder
      </button>
    </form>
  )
}
