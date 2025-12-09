import { useMemo } from 'react'
import { Button } from 'react-aria-components'
import type { AuditTimelineBucket } from '../../types/agentAudit'

type AuditTimelineProps = {
  buckets: AuditTimelineBucket[]
  loading: boolean
  error: string | null
  selectedDay: string | null
  onSelect: (day: string) => void
  processingActive: boolean
}

function formatLabel(date: Date): string {
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function AuditTimeline({ buckets, loading, error, selectedDay, onSelect, processingActive }: AuditTimelineProps) {
  const orderedBuckets = useMemo(
    () =>
      [...buckets].sort(
        (a, b) => new Date(b.day).getTime() - new Date(a.day).getTime(),
      ),
    [buckets],
  )

  const maxCount = useMemo(() => {
    const max = orderedBuckets.reduce((maxVal, bucket) => Math.max(maxVal, bucket.count || 0), 0)
    return max > 0 ? max : 1
  }, [orderedBuckets])

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex h-full flex-col gap-4 p-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[10px] uppercase tracking-[0.22em] text-slate-500">Timeline</div>
            <div className="text-sm font-semibold text-slate-900">Jump to activity</div>
          </div>
          {processingActive ? (
            <span className="rounded-full bg-emerald-50 px-3 py-1 text-[11px] font-semibold text-emerald-700 ring-1 ring-emerald-200">
              Live
            </span>
          ) : null}
        </div>

        {loading ? <div className="text-xs text-slate-500">Loading timeline…</div> : null}
        {error ? <div className="text-xs text-rose-600">{error}</div> : null}

        {!loading && !error ? (
          <div className="custom-scrollbar flex-1 space-y-2 overflow-y-auto pr-1">
            {orderedBuckets.map((bucket) => {
              const bucketDate = new Date(`${bucket.day}T00:00:00`)
              const label = formatLabel(bucketDate)
              const isSelected = selectedDay === bucket.day
              const width = Math.max(18, Math.round((bucket.count / maxCount) * 110) + 24)
              return (
                <Button
                  key={bucket.day}
                  onPress={() => onSelect(bucket.day)}
                  className="group w-full rounded-lg px-2 py-1 text-left transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
                >
                  <div className="flex items-center gap-3">
                    <div
                      className={`h-3 rounded-full transition-all ${isSelected ? 'bg-slate-900 shadow-[0_0_0_3px_rgba(15,23,42,0.12)]' : 'bg-slate-200 group-hover:bg-slate-300'}`}
                      style={{ width }}
                    />
                    <div className="flex flex-1 flex-col">
                      <span className={`text-[11px] font-semibold ${isSelected ? 'text-slate-900' : 'text-slate-700'}`}>
                        {label}
                      </span>
                      <span className="text-[10px] text-slate-500">{bucket.count} events</span>
                    </div>
                  </div>
                </Button>
              )
            })}
            {!buckets.length ? <div className="text-xs text-slate-500">No activity yet.</div> : null}
          </div>
        ) : null}
      </div>
    </div>
  )
}
