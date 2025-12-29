import { ChevronRight } from 'lucide-react'

import type { AgentFsNode } from './types'

type FileManagerBreadcrumbsProps = {
  breadcrumbs: AgentFsNode[]
  onNavigate: (folderId: string | null) => void
}

export function FileManagerBreadcrumbs({ breadcrumbs, onNavigate }: FileManagerBreadcrumbsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
      <button
        type="button"
        className="font-semibold text-blue-700 transition hover:text-blue-900 disabled:text-slate-400"
        onClick={() => onNavigate(null)}
        disabled={!breadcrumbs.length}
      >
        Root
      </button>
      {breadcrumbs.map((folder) => (
        <span key={folder.id} className="inline-flex items-center gap-2">
          <ChevronRight className="h-4 w-4 text-slate-400" aria-hidden="true" />
          <button
            type="button"
            className="font-semibold text-blue-700 transition hover:text-blue-900"
            onClick={() => onNavigate(folder.id)}
          >
            {folder.name}
          </button>
        </span>
      ))}
    </div>
  )
}
