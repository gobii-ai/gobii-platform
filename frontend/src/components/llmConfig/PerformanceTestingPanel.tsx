import { ChevronDown, ChevronUp, Clock3, LoaderCircle, Search } from 'lucide-react'
import { Fragment, useMemo, useState, type FormEvent } from 'react'

import * as llmApi from '../../api/llmConfig'
import { SectionCard } from './SectionCard'
import {
  button,
  DEFAULT_PERFORMANCE_INPUT_TOKEN_SIZES,
  formatCost,
  formatNullableNumber,
  formatTokenCount,
  PERFORMANCE_INPUT_TOKEN_SIZE_OPTIONS,
} from './shared'

export function PerformanceTestingPanel({
  persistentEndpoints,
  result,
  isRunning,
  onRun,
}: {
  persistentEndpoints: llmApi.ProviderEndpoint[]
  result: llmApi.LlmPerformanceTestResponse | null
  isRunning: boolean
  onRun: (payload: { endpoint_ids: string[]; samples_per_endpoint: number; input_token_sizes: number[] }) => Promise<void>
}) {
  const [endpointQuery, setEndpointQuery] = useState('')
  const [selectedEndpointIds, setSelectedEndpointIds] = useState<string[]>([])
  const [inputTokenSizes, setInputTokenSizes] = useState<number[]>(DEFAULT_PERFORMANCE_INPUT_TOKEN_SIZES)
  const [samplesInput, setSamplesInput] = useState('1')
  const [validationMessage, setValidationMessage] = useState<string | null>(null)
  const [expandedRowKeys, setExpandedRowKeys] = useState<Set<string>>(new Set())

  const enabledEndpoints = useMemo(
    () => persistentEndpoints.filter((endpoint) => endpoint.enabled),
    [persistentEndpoints],
  )
  const endpointSearchTerm = endpointQuery.trim().toLowerCase()
  const filteredEndpoints = useMemo(() => {
    if (!endpointSearchTerm) return enabledEndpoints
    return enabledEndpoints.filter((endpoint) => {
      const searchBlob = [
        endpoint.label,
        endpoint.key,
        endpoint.model,
        endpoint.api_base,
        endpoint.provider_id,
      ].filter(Boolean).join(' ').toLowerCase()
      return searchBlob.includes(endpointSearchTerm)
    })
  }, [enabledEndpoints, endpointSearchTerm])

  const sortedResults = useMemo(() => {
    const rows = (result?.endpoints ?? []).flatMap((entry) => (
      entry.input_sizes.map((inputSize) => ({
        endpoint: entry.endpoint,
        inputSize,
      }))
    ))
    return rows.sort((a, b) => {
      if (a.inputSize.requested_input_tokens !== b.inputSize.requested_input_tokens) {
        return a.inputSize.requested_input_tokens - b.inputSize.requested_input_tokens
      }
      const aLatency = a.inputSize.summary.latency_ms.avg ?? Number.POSITIVE_INFINITY
      const bLatency = b.inputSize.summary.latency_ms.avg ?? Number.POSITIVE_INFINITY
      return aLatency - bLatency
    })
  }, [result?.endpoints])

  const toggleEndpoint = (endpointId: string) => {
    setSelectedEndpointIds((current) => (
      current.includes(endpointId)
        ? current.filter((id) => id !== endpointId)
        : [...current, endpointId]
    ))
  }

  const toggleExpanded = (rowKey: string) => {
    setExpandedRowKeys((current) => {
      const next = new Set(current)
      if (next.has(rowKey)) {
        next.delete(rowKey)
      } else {
        next.add(rowKey)
      }
      return next
    })
  }

  const toggleInputTokenSize = (size: number) => {
    setInputTokenSizes((current) => (
      current.includes(size)
        ? current.filter((entry) => entry !== size)
        : [...current, size].sort((a, b) => a - b)
    ))
  }

  const parseInputTokenSizes = () => {
    if (!inputTokenSizes.length) {
      return { message: 'Select at least one input token size.' }
    }
    return { sizes: inputTokenSizes }
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const samples = Number(samplesInput)
    if (!Number.isInteger(samples) || samples < 1 || samples > 10) {
      setValidationMessage('Samples must be a whole number from 1 to 10.')
      return
    }
    if (selectedEndpointIds.length < 1) {
      setValidationMessage('Select at least one persistent endpoint.')
      return
    }
    const tokenSizeResult = parseInputTokenSizes()
    if (tokenSizeResult.message || !tokenSizeResult.sizes) {
      setValidationMessage(tokenSizeResult.message || 'Enter valid input token sizes.')
      return
    }
    setValidationMessage(null)
    await onRun({
      endpoint_ids: selectedEndpointIds,
      samples_per_endpoint: samples,
      input_token_sizes: tokenSizeResult.sizes,
    })
  }

  return (
    <SectionCard
      title="Performance testing"
      description="Run synthetic input-size benchmarks against persistent endpoints and compare response metrics."
    >
      <form className="space-y-5" onSubmit={handleSubmit}>
        <div className="grid gap-4 xl:grid-cols-[minmax(320px,1fr)_minmax(300px,0.9fr)_180px]">
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <label className="text-sm font-semibold text-slate-800">Persistent endpoints</label>
              <span className="text-xs text-slate-500">{selectedEndpointIds.length}/8 selected</span>
            </div>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-3 size-4 text-blue-500" />
              <input
                value={endpointQuery}
                onChange={(event) => setEndpointQuery(event.target.value)}
                placeholder="Search LLMs"
                className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/30"
              />
            </div>
            <div className="max-h-52 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2">
              {filteredEndpoints.map((endpoint) => (
                <label key={endpoint.id} className="flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2 transition hover:bg-blue-50">
                  <input
                    type="checkbox"
                    checked={selectedEndpointIds.includes(endpoint.id)}
                    onChange={() => toggleEndpoint(endpoint.id)}
                    disabled={!selectedEndpointIds.includes(endpoint.id) && selectedEndpointIds.length >= 8}
                    className="mt-1 size-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-slate-900">{endpoint.label}</span>
                    <span className="block truncate font-mono text-xs text-slate-500">{endpoint.key}</span>
                  </span>
                </label>
              ))}
              {enabledEndpoints.length === 0 && (
                <p className="px-2 py-3 text-sm text-amber-700">No enabled persistent endpoints are available.</p>
              )}
              {enabledEndpoints.length > 0 && filteredEndpoints.length === 0 && (
                <p className="px-2 py-3 text-sm text-amber-700">No LLMs match that search.</p>
              )}
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <label className="text-sm font-semibold text-slate-800">Input token sizes</label>
              <span className="text-xs text-slate-500">{inputTokenSizes.length}/3 selected</span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {PERFORMANCE_INPUT_TOKEN_SIZE_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className={`flex cursor-pointer items-center justify-center gap-2 rounded-xl border px-3 py-2.5 text-sm font-semibold transition ${
                    inputTokenSizes.includes(option.value)
                      ? 'border-blue-500 bg-blue-50 text-blue-800 ring-1 ring-blue-500/20'
                      : 'border-slate-200 bg-white text-slate-700 hover:bg-blue-50'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={inputTokenSizes.includes(option.value)}
                    onChange={() => toggleInputTokenSize(option.value)}
                    className="size-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-semibold text-slate-800" htmlFor="llm-performance-samples">
              Samples
            </label>
            <input
              id="llm-performance-samples"
              type="number"
              min={1}
              max={10}
              step={1}
              value={samplesInput}
              onChange={(event) => setSamplesInput(event.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/30"
            />
            <button
              type="submit"
              className={button.primary}
              disabled={isRunning || enabledEndpoints.length === 0}
            >
              {isRunning ? <LoaderCircle className="size-4 animate-spin" /> : <Clock3 className="size-4" />}
              Run test
            </button>
          </div>
        </div>

        {validationMessage && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            {validationMessage}
          </div>
        )}
      </form>

      {result && (
        <div className="mt-6 space-y-4">
          <div className="flex flex-col gap-2 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-blue-950">Synthetic input benchmark</p>
              <p className="text-xs text-blue-800">
                {result.input_token_sizes.map((size) => formatTokenCount(size)).join(', ')} input tokens · {result.samples_per_endpoint} samples per endpoint
              </p>
            </div>
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-blue-50">
                <tr>
                  <th className="px-4 py-3 text-left font-semibold text-slate-700">Endpoint</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-700">Requested input</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-700">Latency avg</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-700">p95</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-700">Tok/sec</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-700">Input tokens</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-700">Output tokens</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-700">Cost</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-700">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sortedResults.map((entry) => {
                  const rowKey = `${entry.endpoint.id}:${entry.inputSize.requested_input_tokens}`
                  const expanded = expandedRowKeys.has(rowKey)
                  const pendingCount = entry.inputSize.samples.filter((sample) => sample.status === 'pending' || sample.status === 'running').length
                  return (
                    <Fragment key={rowKey}>
                      <tr className="align-top">
                        <td className="px-4 py-3">
                          <button
                            type="button"
                            className="flex items-start gap-2 text-left"
                            onClick={() => toggleExpanded(rowKey)}
                          >
                            {expanded ? <ChevronUp className="mt-0.5 size-4 text-blue-600" /> : <ChevronDown className="mt-0.5 size-4 text-blue-600" />}
                            <span>
                              <span className="block font-semibold text-slate-900">{entry.endpoint.label}</span>
                              <span className="block font-mono text-xs text-slate-500">{entry.endpoint.key}</span>
                            </span>
                          </button>
                        </td>
                        <td className="px-4 py-3 text-slate-700">
                          <span className="block font-semibold text-slate-800">{formatTokenCount(entry.inputSize.requested_input_tokens)}</span>
                          <span className="block text-xs text-slate-500">est. {formatTokenCount(entry.inputSize.estimated_prompt_tokens)}</span>
                        </td>
                        <td className="px-4 py-3 text-slate-700">{formatNullableNumber(entry.inputSize.summary.latency_ms.avg, ' ms')}</td>
                        <td className="px-4 py-3 text-slate-700">{formatNullableNumber(entry.inputSize.summary.latency_ms.p95, ' ms')}</td>
                        <td className="px-4 py-3 text-slate-700">{formatNullableNumber(entry.inputSize.summary.avg_completion_tokens_per_second)}</td>
                        <td className="px-4 py-3 text-slate-700">{formatTokenCount(entry.inputSize.summary.total_prompt_tokens)}</td>
                        <td className="px-4 py-3 text-slate-700">{formatTokenCount(entry.inputSize.summary.total_completion_tokens)}</td>
                        <td className="px-4 py-3 text-slate-700">{formatCost(entry.inputSize.summary.total_cost)}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${entry.inputSize.summary.error_count || pendingCount ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'}`}>
                            {entry.inputSize.summary.success_count} ok / {entry.inputSize.summary.error_count} failed{pendingCount ? ` / ${pendingCount} pending` : ''}
                          </span>
                        </td>
                      </tr>
                      {expanded && (
                        <tr>
                          <td colSpan={9} className="px-4 pb-4">
                            <div className="overflow-x-auto rounded-xl border border-blue-100">
                              <table className="min-w-full divide-y divide-blue-100 text-xs">
                                <thead className="bg-white">
                                  <tr>
                                    <th className="px-3 py-2 text-left font-semibold text-slate-600">Sample</th>
                                    <th className="px-3 py-2 text-left font-semibold text-slate-600">Latency</th>
                                    <th className="px-3 py-2 text-left font-semibold text-slate-600">Tok/sec</th>
                                    <th className="px-3 py-2 text-left font-semibold text-slate-600">Input</th>
                                    <th className="px-3 py-2 text-left font-semibold text-slate-600">Output</th>
                                    <th className="px-3 py-2 text-left font-semibold text-slate-600">Cost</th>
                                    <th className="px-3 py-2 text-left font-semibold text-slate-600">Result</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-blue-50 bg-white">
                                  {entry.inputSize.samples.map((sample) => (
                                    <tr key={`${rowKey}:${sample.sample}`}>
                                      <td className="px-3 py-2 font-mono text-slate-600">#{sample.sample}</td>
                                      <td className="px-3 py-2 text-slate-700">{formatNullableNumber(sample.latency_ms, ' ms')}</td>
                                      <td className="px-3 py-2 text-slate-700">{formatNullableNumber(sample.completion_tokens_per_second)}</td>
                                      <td className="px-3 py-2 text-slate-700">
                                        {formatTokenCount(sample.prompt_tokens)}
                                        {sample.ok && !sample.usage_returned ? <span className="ml-2 text-amber-700">no usage returned</span> : null}
                                      </td>
                                      <td className="px-3 py-2 text-slate-700">{formatTokenCount(sample.completion_tokens)}</td>
                                      <td className="px-3 py-2 text-slate-700">{formatCost(sample.total_cost)}</td>
                                      <td className="max-w-xl px-3 py-2 text-slate-700">
                                        {sample.status === 'pending' || sample.status === 'running' ? (
                                          <span className="font-semibold text-amber-700">{sample.status === 'running' ? 'Running...' : 'Pending'}</span>
                                        ) : sample.ok ? (
                                          <span>
                                            <span className="font-semibold text-slate-800">{sample.response_type || 'content'}:</span>{' '}
                                            {sample.preview || 'No preview'}
                                          </span>
                                        ) : (
                                          <span className="text-rose-700">{sample.error || 'Sample failed'}</span>
                                        )}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </SectionCard>
  )
}
