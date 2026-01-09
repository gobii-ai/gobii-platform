import { Filter } from 'lucide-react'

type FilterOption = {
  key: string
  label: string
}

type AgentAuditFiltersMenuProps = {
  filtersOpen: boolean
  onToggle: () => void
  filters: Record<string, boolean>
  eventFilters: FilterOption[]
  completionFilters: FilterOption[]
  onFilterChange: (key: string, value: boolean) => void
}

export function AgentAuditFiltersMenu({
  filtersOpen,
  onToggle,
  filters,
  eventFilters,
  completionFilters,
  onFilterChange,
}: AgentAuditFiltersMenuProps) {
  return (
    <div className="relative">
      <button
        type="button"
        className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 shadow-sm transition hover:border-slate-300 hover:text-slate-900"
        onClick={onToggle}
        aria-expanded={filtersOpen}
        title="Filters"
        aria-label="Filters"
      >
        <Filter className="h-4 w-4" aria-hidden />
      </button>
      {filtersOpen ? (
        <div className="absolute right-0 z-30 mt-2 w-64 rounded-xl border border-slate-200 bg-white/95 p-3 text-sm shadow-xl backdrop-blur">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Event types</div>
          <div className="space-y-2 text-slate-800">
            {eventFilters.map((filter) => (
              <label key={filter.key} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                  checked={Boolean(filters[filter.key])}
                  onChange={(event) => onFilterChange(filter.key, event.target.checked)}
                />
                <span>{filter.label}</span>
              </label>
            ))}
          </div>
          <div className="mt-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Completion types</div>
          <div className="mt-2 space-y-2 text-slate-800">
            {completionFilters.map((filter) => (
              <label key={filter.key} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-slate-300 text-slate-700 focus:ring-slate-600"
                  checked={Boolean(filters[filter.key])}
                  onChange={(event) => onFilterChange(filter.key, event.target.checked)}
                  disabled={!filters.completions}
                />
                <span className={filters.completions ? '' : 'text-slate-400'}>{filter.label}</span>
              </label>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}
