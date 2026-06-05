import { useState } from 'react'

import * as llmApi from '../../api/llmConfig'
import {
  actionKey,
  buildPerformanceResultShell,
  LLM_PERFORMANCE_REQUEST_CONCURRENCY,
  performanceErrorMessage,
  runWithConcurrency,
  updatePerformanceResultSample,
  type AsyncFeedback,
} from './shared'

type UseLlmPerformanceRunnerArgs = {
  endpointChoices: llmApi.EndpointChoices
  runWithFeedback: AsyncFeedback['runWithFeedback']
}

export function useLlmPerformanceRunner({ endpointChoices, runWithFeedback }: UseLlmPerformanceRunnerArgs) {
  const [performanceResult, setPerformanceResult] = useState<llmApi.LlmPerformanceTestResponse | null>(null)

  const handleRunPerformanceTest = async (payload: { endpoint_ids: string[]; samples_per_endpoint: number; input_token_sizes: number[] }) => {
    const selectedEndpoints = payload.endpoint_ids
      .map((endpointId) => endpointChoices.persistent_endpoints.find((endpoint) => endpoint.id === endpointId))
      .filter((endpoint): endpoint is llmApi.ProviderEndpoint => Boolean(endpoint))
    const initialResult = buildPerformanceResultShell(
      selectedEndpoints,
      payload.input_token_sizes,
      payload.samples_per_endpoint,
    )
    setPerformanceResult(initialResult)

    await runWithFeedback(
      () => {
        const tasks: Array<() => Promise<void>> = []
        for (const endpoint of selectedEndpoints) {
          for (const inputTokenSize of payload.input_token_sizes) {
            for (let sampleNumber = 1; sampleNumber <= payload.samples_per_endpoint; sampleNumber += 1) {
              tasks.push(async () => {
                setPerformanceResult((current) => current ? updatePerformanceResultSample(
                  current,
                  endpoint.id,
                  inputTokenSize,
                  sampleNumber,
                  { sample: sampleNumber, ok: false, status: 'running' },
                ) : current)

                try {
                  const response = await llmApi.runPerformanceTest({
                    endpoint_id: endpoint.id,
                    input_token_size: inputTokenSize,
                    sample_number: sampleNumber,
                  })
                  setPerformanceResult((current) => current ? updatePerformanceResultSample(
                    current,
                    response.endpoint.id,
                    response.input_size.requested_input_tokens,
                    response.sample.sample,
                    response.sample,
                    {
                      endpoint: response.endpoint,
                      inputSize: response.input_size,
                    },
                  ) : current)
                } catch (error) {
                  setPerformanceResult((current) => current ? updatePerformanceResultSample(
                    current,
                    endpoint.id,
                    inputTokenSize,
                    sampleNumber,
                    {
                      sample: sampleNumber,
                      ok: false,
                      error: performanceErrorMessage(error),
                      usage_returned: false,
                    },
                  ) : current)
                }
              })
            }
          }
        }
        return runWithConcurrency(tasks, LLM_PERFORMANCE_REQUEST_CONCURRENCY)
      },
      {
        successMessage: 'Performance test complete',
        label: 'Running performance test…',
        busyKey: actionKey('llm-performance-test'),
        context: 'Performance testing',
      },
    )
  }

  return { performanceResult, handleRunPerformanceTest }
}
