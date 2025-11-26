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
  currentFingerprint,
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
      <div className="space-y-6">
        {/* Current run info */}
        {(currentFingerprint || currentCodeVersion || currentModel) && (
          <div className="rounded-lg bg-slate-50 p-3 text-xs space-y-1">
            <p className="font-semibold text-slate-600 uppercase tracking-wider text-[10px]">Current Run</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-slate-600">
              {currentCodeVersion && (
                <span>
                  Code: <span className="font-mono text-slate-800">{currentCodeVersion}</span>
                </span>
              )}
              {currentModel && (
                <span>
                  Model: <span className="font-medium text-slate-800">{currentModel}</span>
                </span>
              )}
              {currentFingerprint && (
                <span>
                  Fingerprint: <span className="font-mono text-slate-800">{currentFingerprint}</span>
                </span>
              )}
            </div>
          </div>
        )}

        {/* Group By Selection */}
        <div>
          <p className="text-sm font-semibold text-slate-900 mb-3">What are you testing?</p>
          <div className="grid grid-cols-3 gap-3">
            {groupByOptions.map((option) => {
              const Icon = option.icon
              const isSelected = groupBy === option.value
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setGroupBy(option.value)}
                  className={`
                    relative flex flex-col items-center text-center p-4 rounded-xl border-2 transition-all
                    ${isSelected
                      ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500'
                      : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                    }
                  `}
                >
                  <div className={`p-2 rounded-lg mb-2 ${isSelected ? 'bg-indigo-100' : 'bg-slate-100'}`}>
                    <Icon className={`w-5 h-5 ${isSelected ? 'text-indigo-600' : 'text-slate-500'}`} />
                  </div>
                  <span className={`text-sm font-semibold ${isSelected ? 'text-indigo-900' : 'text-slate-700'}`}>
                    {option.label}
                  </span>
                  <span className={`text-xs mt-1 ${isSelected ? 'text-indigo-600' : 'text-slate-500'}`}>
                    {option.description}
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        {/* Filters Row */}
        <div className="flex gap-4">
          {/* Run Type Filter */}
          <div className="flex-1">
            <label className="block text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2">
              Run Type
            </label>
            <select
              value={runType || ''}
              onChange={(e) => setRunType(e.target.value as EvalRunType || null)}
              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            >
              <option value="">Any</option>
              <option value="official">Official only</option>
              <option value="one_off">One-off only</option>
            </select>
          </div>
        </div>

        {/* Tier Selection */}
        <div>
          <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2">
            Comparison Strictness
          </p>
          <div className="space-y-2">
            {tierOptions.map((option) => {
              const isSelected = tier === option.value
              const isHistorical = option.value === 'historical'
              return (
                <label
                  key={option.value}
                  className={`
                    flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-all
                    ${isSelected
                      ? 'border-indigo-300 bg-indigo-50/50'
                      : 'border-slate-200 bg-white hover:border-slate-300'
                    }
                  `}
                >
                  <input
                    type="radio"
                    name="tier"
                    value={option.value}
                    checked={isSelected}
                    onChange={(e) => setTier(e.target.value as ComparisonTier)}
                    className="mt-0.5 h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-slate-300"
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className={`text-sm font-semibold ${isSelected ? 'text-indigo-900' : 'text-slate-700'}`}>
                        {option.label}
                      </span>
                      {isHistorical && (
                        <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
                      )}
                    </div>
                    <span className={`text-xs ${isSelected ? 'text-indigo-600' : 'text-slate-500'}`}>
                      {option.description}
                    </span>
                  </div>
                </label>
              )
            })}
          </div>
        </div>

        {/* Warning for historical tier */}
        {tier === 'historical' && (
          <div className="flex items-start gap-3 p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-800">
            <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
            <div className="text-xs">
              <p className="font-semibold">Fingerprint Mismatch Warning</p>
              <p className="mt-1">
                Historical comparisons may include runs where the eval code itself changed.
                Results should be interpreted with caution.
              </p>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}
