import { useState } from 'react'
import { GitBranch, Cpu, Settings, AlertTriangle, BarChart3 } from 'lucide-react'

import { Modal } from '../common/Modal'
import type { ComparisonTier, ComparisonGroupBy, EvalRunType } from '../../api/evals'

export type CompareConfig = {
  tier: ComparisonTier
  groupBy: ComparisonGroupBy | null
  runType: EvalRunType | null
}

type CompareModalProps = {
  onClose: () => void
  onCompare: (config: CompareConfig) => void
  comparableCount?: number
  currentFingerprint?: string
  currentCodeVersion?: string
  currentModel?: string
  currentRunType?: EvalRunType
}

const groupByOptions: { value: ComparisonGroupBy; label: string; description: string; icon: typeof GitBranch }[] = [
  {
    value: 'code_version',
    label: 'Code Changes',
    description: 'Same model, compare across commits',
    icon: GitBranch,
  },
  {
    value: 'primary_model',
    label: 'Model Choice',
    description: 'Same code, compare different models',
    icon: Cpu,
  },
  {
    value: 'llm_profile',
    label: 'LLM Config',
    description: 'Same code + model, compare configs',
    icon: Settings,
  },
]

const tierOptions: { value: ComparisonTier; label: string; description: string }[] = [
  {
    value: 'strict',
    label: 'Strict',
    description: 'Same eval code + LLM profile lineage',
  },
  {
    value: 'pragmatic',
    label: 'Pragmatic',
    description: 'Same eval code, any config',
  },
  {
    value: 'historical',
    label: 'Historical',
    description: 'Same scenario name (may include changed evals)',
  },
]

export function CompareModal({
  onClose,
  onCompare,
  comparableCount,
  currentCodeVersion,
  currentModel,
  currentRunType,
}: CompareModalProps) {
  const [groupBy, setGroupBy] = useState<ComparisonGroupBy | null>('code_version')
  const [tier, setTier] = useState<ComparisonTier>('pragmatic')
  // Default to matching current run type, or "Any" if not specified
  const [runType, setRunType] = useState<EvalRunType | null>(currentRunType ?? null)

  const handleCompare = () => {
    onCompare({ tier, groupBy, runType })
  }

  const subtitle = comparableCount != null && comparableCount > 0
    ? `${comparableCount} comparable run${comparableCount !== 1 ? 's' : ''} found with same eval code`
    : 'Find runs to compare against'

  return (
    <Modal
      title="Compare Eval Runs"
      subtitle={subtitle}
      icon={BarChart3}
      iconBgClass="bg-indigo-100"
      iconColorClass="text-indigo-600"
      onClose={onClose}
      widthClass="sm:max-w-xl"
      footer={
        <>
          <button
            type="button"
            onClick={handleCompare}
            className="inline-flex items-center justify-center gap-2 px-5 py-2.5 text-sm font-semibold text-white bg-indigo-600 rounded-lg shadow-sm hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition-all"
          >
            View Comparison
          </button>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center justify-center px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-200 rounded-lg shadow-sm hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-slate-500 transition-all"
          >
            Cancel
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {/* Current run info - compact inline */}
        {(currentCodeVersion || currentModel) && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
            {currentCodeVersion && (
              <span>Code: <span className="font-mono text-slate-700">{currentCodeVersion}</span></span>
            )}
            {currentModel && (
              <span>Model: <span className="font-medium text-slate-700">{currentModel}</span></span>
            )}
          </div>
        )}

        {/* Group By Selection - more compact */}
        <div>
          <p className="text-sm font-semibold text-slate-900 mb-2">What are you testing?</p>
          <div className="grid grid-cols-3 gap-2">
            {groupByOptions.map((option) => {
              const Icon = option.icon
              const isSelected = groupBy === option.value
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setGroupBy(option.value)}
                  className={`
                    relative flex flex-col items-center text-center p-3 rounded-lg border-2 transition-all
                    ${isSelected
                      ? 'border-indigo-500 bg-indigo-50'
                      : 'border-slate-200 bg-white hover:border-slate-300'
                    }
                  `}
                >
                  <Icon className={`w-4 h-4 mb-1.5 ${isSelected ? 'text-indigo-600' : 'text-slate-400'}`} />
                  <span className={`text-xs font-semibold ${isSelected ? 'text-indigo-900' : 'text-slate-700'}`}>
                    {option.label}
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        {/* Tier + Run Type in a row */}
        <div className="flex gap-4">
          {/* Tier Selection - horizontal pills */}
          <div className="flex-1">
            <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2">Strictness</p>
            <div className="flex gap-1 p-1 bg-slate-100 rounded-lg">
              {tierOptions.map((option) => {
                const isSelected = tier === option.value
                const isHistorical = option.value === 'historical'
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setTier(option.value)}
                    className={`
                      flex-1 px-2 py-1.5 text-xs font-medium rounded-md transition-all flex items-center justify-center gap-1
                      ${isSelected
                        ? 'bg-white text-indigo-700 shadow-sm'
                        : 'text-slate-600 hover:text-slate-800'
                      }
                    `}
                    title={option.description}
                  >
                    {option.label}
                    {isHistorical && <AlertTriangle className="w-3 h-3 text-amber-500" />}
                  </button>
                )
              })}
            </div>
          </div>

          {/* Run Type Filter */}
          <div className="w-32">
            <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2">Run Type</p>
            <select
              value={runType || ''}
              onChange={(e) => setRunType(e.target.value as EvalRunType || null)}
              className="w-full px-2 py-1.5 text-xs border border-slate-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">Any</option>
              <option value="official">Official</option>
              <option value="one_off">One-off</option>
            </select>
          </div>
        </div>

        {/* Warning for historical tier - compact */}
        {tier === 'historical' && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs">
            <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0" />
            <span>Historical may include runs where eval code changed.</span>
          </div>
        )}
      </div>
    </Modal>
  )
}
