import { ChevronRight } from 'lucide-react'

import type { AgentFsNode } from './types'

type FileManagerBreadcrumbsProps = {
  breadcrumbs: AgentFsNode[]
  onNavigate: (folderId: string | null) => void
}

export function FileManagerBreadcrumbs({ breadcrumbs, onNavigate }: FileManagerBreadcrumbsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-slate-400">
      <button
        type="button"
        className="font-semibold text-blue-300 transition hover:text-blue-200 disabled:text-slate-600"
        onClick={() => onNavigate(null)}
        disabled={!breadcrumbs.length}
      >
        Root
      </button>
      {breadcrumbs.map((folder) => (
        <span key={folder.id} className="inline-flex items-center gap-2">
          <ChevronRight className="h-4 w-4 text-slate-500" aria-hidden="true" />
          <button
            type="button"
            className="font-semibold text-blue-300 transition hover:text-blue-200"
            onClick={() => onNavigate(folder.id)}
          >
            {folder.name}
          </button>
        </span>
      ))}
    </div>
  )
}
