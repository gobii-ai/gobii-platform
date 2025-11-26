import { useMemo } from 'react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { AlertTriangle, TrendingUp, TrendingDown, Minus } from 'lucide-react'

import type { ComparisonResponse, ComparisonGroup, ComparisonRunSummary, ComparisonGroupBy } from '../../api/evals'

echarts.use([BarChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

type CompareResultsViewProps = {
  data: ComparisonResponse
}

const formatCurrency = (value: number | null, digits = 4) => {
  if (value == null) return '—'
  return `$${value.toFixed(digits)}`
}

const formatPercent = (value: number | null) => {
  if (value == null) return '—'
  return `${Math.round(value * 100)}%`
}

const formatTokens = (value: number | null) => {
  if (value == null) return '—'
  return value.toLocaleString()
}

const formatTs = (value: string | null | undefined) => {
  if (!value) return '—'
  try {
    const date = new Date(value)
    return date.toLocaleDateString()
  } catch {
    return value
  }
}

// Color palette for charts
const chartColors = [
  '#6366f1', // indigo
  '#14b8a6', // teal
  '#f97316', // orange
  '#ef4444', // red
  '#0ea5e9', // sky
  '#a855f7', // purple
  '#22c55e', // green
  '#f472b6', // pink
]

function PassRateChart({ groups }: { groups: ComparisonGroup[] }) {
  const option = useMemo(() => {
    const categories = groups.map((g) => g.value || 'Unknown')
    const passRates = groups.map((g) => Math.round(g.pass_rate))

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: Array<{ name: string; value: number }>) => {
          const item = params[0]
          return `${item.name}<br/>Pass Rate: ${item.value}%`
        },
      },
      grid: {
        top: 20,
        left: 50,
        right: 20,
        bottom: 40,
      },
      xAxis: {
        type: 'category',
        data: categories,
        axisLabel: {
          interval: 0,
          rotate: categories.length > 4 ? 30 : 0,
          fontSize: 11,
        },
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 100,
        axisLabel: {
          formatter: '{value}%',
        },
      },
      series: [
        {
          type: 'bar',
          data: passRates.map((value, index) => ({
            value,
            itemStyle: { color: chartColors[index % chartColors.length] },
          })),
          barMaxWidth: 60,
          label: {
            show: true,
            position: 'top',
            formatter: '{c}%',
            fontSize: 12,
            fontWeight: 'bold',
          },
        },
      ],
    }
  }, [groups])

  return (
    <div className="h-64">
      <ReactEChartsCore echarts={echarts} option={option} notMerge style={{ height: '100%', width: '100%' }} />
    </div>
  )
}

function CostChart({ groups }: { groups: ComparisonGroup[] }) {
  const option = useMemo(() => {
    const categories = groups.map((g) => g.value || 'Unknown')
    const costs = groups.map((g) => g.avg_cost)

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: Array<{ name: string; value: number }>) => {
          const item = params[0]
          return `${item.name}<br/>Avg Cost: $${item.value.toFixed(4)}`
        },
      },
      grid: {
        top: 20,
        left: 60,
        right: 20,
        bottom: 40,
      },
      xAxis: {
        type: 'category',
        data: categories,
        axisLabel: {
          interval: 0,
          rotate: categories.length > 4 ? 30 : 0,
          fontSize: 11,
        },
      },
      yAxis: {
        type: 'value',
        min: 0,
        axisLabel: {
          formatter: (value: number) => `$${value.toFixed(3)}`,
        },
      },
      series: [
        {
          type: 'bar',
          data: costs.map((value, index) => ({
            value,
            itemStyle: { color: chartColors[index % chartColors.length] },
          })),
          barMaxWidth: 60,
          label: {
            show: true,
            position: 'top',
            formatter: (params: { value: number }) => `$${params.value.toFixed(4)}`,
            fontSize: 11,
          },
        },
      ],
    }
  }, [groups])

  return (
    <div className="h-64">
      <ReactEChartsCore echarts={echarts} option={option} notMerge style={{ height: '100%', width: '100%' }} />
    </div>
  )
}

function TrendIndicator({ current, baseline, inverse = false }: { current: number | null; baseline: number | null; inverse?: boolean }) {
  if (current == null || baseline == null || baseline === 0) {
    return <Minus className="w-4 h-4 text-slate-400" />
  }

  const percentChange = ((current - baseline) / baseline) * 100
  const isImproved = inverse ? percentChange < 0 : percentChange > 0

  if (Math.abs(percentChange) < 1) {
    return <Minus className="w-4 h-4 text-slate-400" />
  }

  return (
    <span className={`flex items-center gap-1 text-xs font-semibold ${isImproved ? 'text-emerald-600' : 'text-rose-600'}`}>
      {isImproved ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
      {Math.abs(percentChange).toFixed(0)}%
    </span>
  )
}

function GroupedResultsTable({ groups, groupBy }: { groups: ComparisonGroup[]; groupBy: ComparisonGroupBy | null }) {
  const sortedGroups = useMemo(() => {
    // Sort by pass rate descending
    return [...groups].sort((a, b) => b.pass_rate - a.pass_rate)
  }, [groups])

  const baseline = sortedGroups[sortedGroups.length - 1] // Use worst as baseline for comparison

  const groupByColumnLabel = useMemo(() => {
    switch (groupBy) {
      case 'code_version':
        return 'Commit'
      case 'primary_model':
        return 'Model'
      case 'llm_profile':
        return 'Profile'
      default:
        return 'Group'
    }
  }, [groupBy])

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="text-left py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">
              {groupByColumnLabel}
            </th>
            <th className="text-center py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Runs</th>
            <th className="text-center py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Pass Rate</th>
            <th className="text-center py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Avg Cost</th>
            <th className="text-center py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Avg Tokens</th>
            <th className="text-center py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Tasks</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {sortedGroups.map((group, index) => (
            <tr key={group.value} className={`hover:bg-slate-50 transition-colors ${group.is_current ? 'bg-indigo-50/50' : ''}`}>
              <td className="py-3 px-4">
                <div className="flex items-center gap-2">
                  <div
                    className="w-3 h-3 rounded-full shrink-0"
                    style={{ backgroundColor: chartColors[index % chartColors.length] }}
                  />
                  <span className="font-mono text-slate-800 font-medium">{group.value || 'Unknown'}</span>
                  {group.is_current && (
                    <span className="text-[10px] font-semibold text-indigo-600 bg-indigo-100 px-1.5 py-0.5 rounded">
                      CURRENT
                    </span>
                  )}
                </div>
              </td>
              <td className="py-3 px-4 text-center text-slate-600">{group.run_count}</td>
              <td className="py-3 px-4 text-center">
                <div className="flex items-center justify-center gap-2">
                  <span className="font-semibold text-slate-900">{Math.round(group.pass_rate)}%</span>
                  {group !== baseline && (
                    <TrendIndicator current={group.pass_rate} baseline={baseline.pass_rate} />
                  )}
                </div>
              </td>
              <td className="py-3 px-4 text-center">
                <div className="flex items-center justify-center gap-2">
                  <span className="font-mono text-slate-700">{formatCurrency(group.avg_cost)}</span>
                  {group !== baseline && (
                    <TrendIndicator current={group.avg_cost} baseline={baseline.avg_cost} inverse />
                  )}
                </div>
              </td>
              <td className="py-3 px-4 text-center font-mono text-slate-600">
                {formatTokens(group.avg_tokens)}
              </td>
              <td className="py-3 px-4 text-center text-slate-600">
                <span className="text-emerald-600 font-medium">{group.passed_tasks}</span>
                <span className="text-slate-400">/{group.total_tasks}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function UngroupedResultsTable({ runs }: { runs: ComparisonRunSummary[] }) {
  const sortedRuns = useMemo(() => {
    return [...runs].sort((a, b) => {
      const dateA = a.started_at ? new Date(a.started_at).getTime() : 0
      const dateB = b.started_at ? new Date(b.started_at).getTime() : 0
      return dateB - dateA // Most recent first
    })
  }, [runs])

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="text-left py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Date</th>
            <th className="text-left py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Commit</th>
            <th className="text-left py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Model</th>
            <th className="text-center py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Pass Rate</th>
            <th className="text-center py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Cost</th>
            <th className="text-center py-3 px-4 text-xs font-bold uppercase tracking-wider text-slate-500">Type</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {sortedRuns.map((run) => (
            <tr key={run.id} className="hover:bg-slate-50 transition-colors">
              <td className="py-3 px-4 text-slate-600">{formatTs(run.started_at)}</td>
              <td className="py-3 px-4">
                <span className="font-mono text-xs bg-slate-100 px-1.5 py-0.5 rounded text-slate-700">
                  {run.code_version || '—'}
                </span>
              </td>
              <td className="py-3 px-4 text-slate-700 font-medium">{run.primary_model || '—'}</td>
              <td className="py-3 px-4 text-center">
                <span className="font-semibold text-slate-900">
                  {run.task_totals?.pass_rate != null ? formatPercent(run.task_totals.pass_rate) : '—'}
                </span>
              </td>
              <td className="py-3 px-4 text-center font-mono text-slate-700">{formatCurrency(run.total_cost)}</td>
              <td className="py-3 px-4 text-center">
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                    run.run_type === 'official'
                      ? 'bg-emerald-100 text-emerald-700'
                      : 'bg-slate-100 text-slate-600'
                  }`}
                >
                  {run.run_type === 'official' ? 'Official' : 'One-off'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function InsightSummary({ groups }: { groups: ComparisonGroup[] }) {
  const insights = useMemo(() => {
    if (groups.length < 2) return null

    const sortedByPassRate = [...groups].sort((a, b) => b.pass_rate - a.pass_rate)
    const sortedByCost = [...groups].sort((a, b) => a.avg_cost - b.avg_cost)

    const best = sortedByPassRate[0]
    const cheapest = sortedByCost[0]

    const messages: string[] = []

    if (best.value) {
      messages.push(`${best.value} has the best pass rate at ${Math.round(best.pass_rate)}%`)
    }

    if (cheapest.value && cheapest.value !== best.value) {
      messages.push(`${cheapest.value} is most cost-effective at ${formatCurrency(cheapest.avg_cost)}/run`)
    }

    // Check if there's a clear winner (best accuracy AND cheapest)
    if (best.value === cheapest.value && best.value) {
      return [`${best.value} offers the best balance of accuracy (${Math.round(best.pass_rate)}%) and cost (${formatCurrency(best.avg_cost)}/run)`]
    }

    return messages
  }, [groups])

  if (!insights || insights.length === 0) return null

  return (
    <div className="rounded-lg bg-indigo-50 border border-indigo-100 p-4">
      <div className="flex items-start gap-3">
        <div className="p-1.5 bg-indigo-100 rounded-lg">
          <TrendingUp className="w-4 h-4 text-indigo-600" />
        </div>
        <div>
          <p className="text-sm font-semibold text-indigo-900">Insights</p>
          <ul className="mt-1 space-y-1">
            {insights.map((insight, i) => (
              <li key={i} className="text-sm text-indigo-700">{insight}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}

function formatGroupBy(groupBy: ComparisonGroupBy | null | undefined): string {
  switch (groupBy) {
    case 'code_version':
      return 'Code Version'
    case 'primary_model':
      return 'Model'
    case 'llm_profile':
      return 'LLM Profile'
    default:
      return 'Group'
  }
}

export function CompareResultsView({ data }: CompareResultsViewProps) {
  const hasGroups = data.groups && data.groups.length > 0
  const hasRuns = data.runs && data.runs.length > 0
  const groupByLabel = formatGroupBy(data.group_by)

  return (
    <div className="space-y-6">
      {/* Fingerprint Warning */}
      {data.fingerprint_warning && (
        <div className="flex items-start gap-3 p-4 rounded-lg bg-amber-50 border border-amber-200">
          <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-amber-800">Fingerprint Mismatch</p>
            <p className="text-sm text-amber-700 mt-1">{data.fingerprint_warning}</p>
          </div>
        </div>
      )}

      {/* Grouped View */}
      {hasGroups && data.groups && (
        <>
          {/* Insight Summary */}
          <InsightSummary groups={data.groups} />

          {/* Charts */}
          <div className="grid md:grid-cols-2 gap-6">
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <h3 className="text-sm font-semibold text-slate-900 mb-4">Pass Rate by {groupByLabel}</h3>
              <PassRateChart groups={data.groups} />
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <h3 className="text-sm font-semibold text-slate-900 mb-4">Average Cost by {groupByLabel}</h3>
              <CostChart groups={data.groups} />
            </div>
          </div>

          {/* Results Table */}
          <div className="rounded-lg border border-slate-200 bg-white overflow-hidden">
            <div className="px-4 py-3 bg-slate-50 border-b border-slate-200">
              <h3 className="text-sm font-semibold text-slate-900">Detailed Comparison</h3>
            </div>
            <GroupedResultsTable groups={data.groups} groupBy={data.group_by ?? null} />
          </div>
        </>
      )}

      {/* Ungrouped View */}
      {!hasGroups && hasRuns && data.runs && (
        <div className="rounded-lg border border-slate-200 bg-white overflow-hidden">
          <div className="px-4 py-3 bg-slate-50 border-b border-slate-200">
            <h3 className="text-sm font-semibold text-slate-900">
              {data.runs.length} Comparable Run{data.runs.length !== 1 ? 's' : ''}
            </h3>
          </div>
          <UngroupedResultsTable runs={data.runs} />
        </div>
      )}

      {/* No Results */}
      {!hasGroups && !hasRuns && (
        <div className="text-center py-12 text-slate-500">
          <p className="text-sm">No comparable runs found with the selected filters.</p>
          <p className="text-xs mt-1">Try adjusting the comparison tier or removing filters.</p>
        </div>
      )}
    </div>
  )
}
