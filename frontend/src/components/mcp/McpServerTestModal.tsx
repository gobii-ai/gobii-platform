import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, Loader2, Search, Stethoscope } from 'lucide-react'

import {
  fetchMcpServerAssignments,
  testMcpServer,
  type McpServer,
  type McpServerAssignmentAgent,
  type McpServerAssignmentResponse,
  type McpServerTestResponse,
  type McpServerTestTool,
} from '../../api/mcp'
import { HttpError } from '../../api/http'
import { Modal } from '../common/Modal'

type McpServerTestModalProps = {
  server: McpServer
  testUrl: string
  assignmentUrl?: string
  requiresAgent: boolean
  onClose: () => void
  onError: (message: string) => void
}

export function McpServerTestModal({
  server,
  testUrl,
  assignmentUrl,
  requiresAgent,
  onClose,
  onError,
}: McpServerTestModalProps) {
  const [selectedAgentId, setSelectedAgentId] = useState('')
  const [agentSearch, setAgentSearch] = useState('')
  const [toolSearch, setToolSearch] = useState('')
  const [statusMessage, setStatusMessage] = useState<string | null>(null)

  const assignmentsQuery = useQuery<McpServerAssignmentResponse, unknown>({
    queryKey: ['mcp-server-test-agents', assignmentUrl],
    queryFn: () => fetchMcpServerAssignments(assignmentUrl || ''),
    enabled: requiresAgent && Boolean(assignmentUrl),
  })

  const testMutation = useMutation<McpServerTestResponse, unknown, string | null>({
    mutationFn: (agentId) => testMcpServer(testUrl, agentId),
    onSuccess: (response) => {
      setStatusMessage(response.message)
      if (response.status === 'error') {
        onError(response.message || 'MCP server test failed.')
      }
    },
    onError: (error) => {
      const message = resolveErrorMessage(error, 'Unable to test MCP server.')
      setStatusMessage(message)
      onError(message)
    },
  })

  useEffect(() => {
    if (!requiresAgent && testMutation.status === 'idle') {
      testMutation.mutate(null)
    }
  }, [requiresAgent, testMutation])

  const filteredAgents = useFilteredAgents(assignmentsQuery.data?.agents, agentSearch)
  const filteredTools = useFilteredTools(testMutation.data?.tools, toolSearch)
  const selectedAgent = assignmentsQuery.data?.agents.find((agent) => agent.id === selectedAgentId)
  const canRun = !requiresAgent || Boolean(selectedAgentId)
  const isSuccess = testMutation.data?.status === 'ok'
  const isTestError = testMutation.data?.status === 'error' || testMutation.isError

  const footer = (
    <>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        onClick={() => testMutation.mutate(requiresAgent ? selectedAgentId : null)}
        disabled={!canRun || testMutation.isPending}
      >
        {testMutation.isPending ? 'Testing...' : testMutation.data ? 'Run Again' : 'Run Test'}
      </button>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 sm:w-auto sm:text-sm"
        onClick={onClose}
        disabled={testMutation.isPending}
      >
        Close
      </button>
    </>
  )

  return (
    <Modal
      title={`Test ${server.displayName}`}
      subtitle={requiresAgent ? "Choose an agent sandbox to discover this server's tools." : 'Discover tools exposed by this MCP server.'}
      onClose={onClose}
      footer={footer}
      icon={Stethoscope}
      widthClass="sm:max-w-4xl"
    >
      <div className="space-y-5">
        {requiresAgent && (
          <div className="space-y-4">
            {assignmentsQuery.isLoading ? (
              <div className="flex items-center gap-2 py-6 text-sm text-slate-500">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Loading eligible agents...
              </div>
            ) : assignmentsQuery.isError ? (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {resolveErrorMessage(assignmentsQuery.error, 'Failed to load eligible agents.')}
              </div>
            ) : (
              <>
                <label className="relative block text-sm text-slate-500">
                  <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
                    <Search className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <input
                    type="search"
                    className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm text-slate-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-blue-500"
                    placeholder="Filter agents"
                    value={agentSearch}
                    onChange={(event) => setAgentSearch(event.target.value)}
                    disabled={testMutation.isPending}
                  />
                </label>
                <div className="max-h-56 overflow-y-auto rounded-lg border border-slate-200">
                  {filteredAgents.length === 0 ? (
                    <div className="px-4 py-6 text-sm text-slate-500">No eligible agents found.</div>
                  ) : (
                    <ul className="divide-y divide-slate-200">
                      {filteredAgents.map((agent) => (
                        <li key={agent.id}>
                          <label className="flex cursor-pointer items-start gap-3 px-4 py-3 hover:bg-slate-50">
                            <input
                              type="radio"
                              className="mt-1 h-4 w-4 border-slate-300 text-blue-600 focus:ring-blue-500"
                              checked={selectedAgentId === agent.id}
                              onChange={() => setSelectedAgentId(agent.id)}
                              disabled={testMutation.isPending}
                            />
                            <AgentSummary agent={agent} />
                          </label>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                {selectedAgent && (
                  <p className="text-xs text-slate-500">
                    Test will run in the sandbox context for {selectedAgent.name}.
                  </p>
                )}
              </>
            )}
          </div>
        )}

        {testMutation.isPending && (
          <div className="flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Discovering MCP tools...
          </div>
        )}

        {statusMessage && (
          <div
            className={`flex items-start gap-2 rounded-lg border px-4 py-3 text-sm ${
              isTestError
                ? 'border-red-200 bg-red-50 text-red-700'
                : isSuccess
                  ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                  : 'border-slate-200 bg-white text-slate-700'
            }`}
          >
            {isTestError ? (
              <AlertCircle className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />
            )}
            <span>{statusMessage}</span>
          </div>
        )}

        {testMutation.data?.status === 'error' && (
          <details className="rounded-lg border border-slate-200 px-4 py-3 text-sm">
            <summary className="cursor-pointer font-medium text-slate-700">Diagnostic details</summary>
            <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100">
              {JSON.stringify(testMutation.data.details, null, 2)}
            </pre>
          </details>
        )}

        {isSuccess && (
          <div className="space-y-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm font-medium text-slate-700">
                {testMutation.data?.tools.length ?? 0} exposed tool{testMutation.data?.tools.length === 1 ? '' : 's'}
              </p>
              <label className="relative block text-sm text-slate-500">
                <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
                  <Search className="h-4 w-4" aria-hidden="true" />
                </span>
                <input
                  type="search"
                  className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm text-slate-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-blue-500 sm:w-72"
                  placeholder="Filter tools"
                  value={toolSearch}
                  onChange={(event) => setToolSearch(event.target.value)}
                />
              </label>
            </div>
            <ToolList tools={filteredTools} />
          </div>
        )}
      </div>
    </Modal>
  )
}

function AgentSummary({ agent }: { agent: McpServerAssignmentAgent }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <p className="text-sm font-medium text-slate-800">{agent.name}</p>
        {!agent.isActive && (
          <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700">
            Inactive
          </span>
        )}
      </div>
      {agent.description && <p className="text-xs text-slate-600">{agent.description}</p>}
    </div>
  )
}

function ToolList({ tools }: { tools: McpServerTestTool[] }) {
  if (tools.length === 0) {
    return <div className="rounded-lg border border-slate-200 px-4 py-6 text-sm text-slate-500">No tools match your filter.</div>
  }
  return (
    <ul className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200">
      {tools.map((tool) => (
        <li key={tool.fullName || tool.toolName} className="space-y-2 px-4 py-3">
          <div>
            <p className="font-mono text-sm font-semibold text-slate-900">{tool.fullName || tool.toolName}</p>
            {tool.description && <p className="mt-1 text-sm text-slate-600">{tool.description}</p>}
          </div>
          <details className="text-xs">
            <summary className="cursor-pointer font-medium text-slate-600">Parameters</summary>
            <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-slate-950 p-3 text-slate-100">
              {JSON.stringify(tool.parameters, null, 2)}
            </pre>
          </details>
        </li>
      ))}
    </ul>
  )
}

function useFilteredAgents(agents: McpServerAssignmentAgent[] | undefined, searchTerm: string): McpServerAssignmentAgent[] {
  return useMemo(() => {
    const items = agents ?? []
    const query = searchTerm.trim().toLowerCase()
    if (!query) {
      return items
    }
    return items.filter((agent) => {
      const name = agent.name.toLowerCase()
      const description = (agent.description || '').toLowerCase()
      return name.includes(query) || description.includes(query)
    })
  }, [agents, searchTerm])
}

function useFilteredTools(tools: McpServerTestTool[] | undefined, searchTerm: string): McpServerTestTool[] {
  return useMemo(() => {
    const items = tools ?? []
    const query = searchTerm.trim().toLowerCase()
    if (!query) {
      return items
    }
    return items.filter((tool) => {
      const name = `${tool.fullName} ${tool.toolName}`.toLowerCase()
      const description = (tool.description || '').toLowerCase()
      return name.includes(query) || description.includes(query)
    })
  }, [tools, searchTerm])
}

function resolveErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof HttpError) {
    if (typeof error.body === 'string' && error.body) {
      return error.body
    }
    if (typeof error.statusText === 'string' && error.statusText) {
      return error.statusText
    }
  }
  if (error && typeof error === 'object' && 'message' in error && typeof (error as { message: unknown }).message === 'string') {
    return (error as { message: string }).message
  }
  return fallback
}
