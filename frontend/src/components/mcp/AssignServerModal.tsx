import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Search } from 'lucide-react'

import {
  fetchMcpServerAssignments,
  updateMcpServerAssignments,
  type McpServer,
  type McpServerAssignmentAgent,
  type McpServerAssignmentResponse,
} from '../../api/mcp'
import { Modal } from '../common/Modal'
import { HttpError } from '../../api/http'

type AssignServerModalProps = {
  server: McpServer
  assignmentUrl: string
  onClose: () => void
  onSuccess: (message: string) => void
  onError: (message: string) => void
}

type SelectionSet = Set<string>

export function AssignServerModal({
  server,
  assignmentUrl,
  onClose,
  onSuccess,
  onError,
}: AssignServerModalProps) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<SelectionSet>(new Set())
  const [searchTerm, setSearchTerm] = useState('')
  const [statusMessage, setStatusMessage] = useState<string | null>(null)

  const assignmentsQuery = useQuery<McpServerAssignmentResponse, unknown>({
    queryKey: ['mcp-server-assignments', assignmentUrl],
    queryFn: () => fetchMcpServerAssignments(assignmentUrl),
  })
  const { data, isLoading, isError, error } = assignmentsQuery

  useEffect(() => {
    if (data) {
      const assignedIds = data.agents.filter((agent) => agent.assigned).map((agent) => agent.id)
      setSelected(new Set(assignedIds))
    }
  }, [data])

  const mutation = useMutation<McpServerAssignmentResponse, unknown, string[]>({
    mutationFn: (agentIds: string[]) => updateMcpServerAssignments(assignmentUrl, agentIds),
    onSuccess: (response) => {
      const message = response.message ?? 'Assignments updated.'
      queryClient.invalidateQueries({ queryKey: ['mcp-server-assignments', assignmentUrl] })
      onSuccess(message)
      onClose()
    },
    onError: (error) => {
      const message = resolveErrorMessage(error, 'Unable to update assignments.')
      setStatusMessage(message)
      onError(message)
    },
  })

  const handleToggle = (agentId: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(agentId)) {
        next.delete(agentId)
      } else {
        next.add(agentId)
      }
      return next
    })
  }

  const handleSelectAll = () => {
    if (!data) {
      return
    }
    const allIds = data.agents.map((agent) => agent.id)
    setSelected(new Set(allIds))
  }

  const handleClearAll = () => {
    setSelected(new Set())
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setStatusMessage(null)
    const payload = Array.from(selected)
    mutation.mutate(payload)
  }

  const filteredAgents = useFilteredAgents(data, searchTerm)

  const assignedCount = selected.size
  const totalAgents = data?.totalAgents ?? 0

  const subtitle = `Assign ${server.displayName} to agents in this ${server.scope === 'organization' ? 'workspace' : 'account'}.`

  const footer = (
    <>
      <button
        type="submit"
        form="assign-server-form"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        disabled={mutation.isPending || isLoading}
      >
        {mutation.isPending ? 'Saving…' : 'Save Assignments'}
      </button>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 sm:w-auto sm:text-sm"
        onClick={onClose}
        disabled={mutation.isPending}
      >
        Cancel
      </button>
    </>
  )

  return (
    <Modal
      title={`Assign Agents – ${server.displayName}`}
      subtitle={subtitle}
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-3xl"
    >
      <form id="assign-server-form" className="space-y-6 p-1" onSubmit={handleSubmit}>
        {statusMessage && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {statusMessage}
          </div>
        )}

        {isLoading ? (
          <div className="flex items-center gap-2 py-12 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading agents…
          </div>
        ) : isError ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {resolveErrorMessage(error, 'Failed to load agents for this server.')}
          </div>
        ) : (
          <div className="space-y-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-3 text-sm text-slate-600">
                <span>
                  {assignedCount} selected
                  {totalAgents ? ` of ${totalAgents}` : ''}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="text-xs font-medium text-indigo-600 hover:text-indigo-700"
                    onClick={handleSelectAll}
                    disabled={!data || data.agents.length === 0 || mutation.isPending}
                  >
                    Select all
                  </button>
                  <span className="text-slate-300">•</span>
                  <button
                    type="button"
                    className="text-xs font-medium text-indigo-600 hover:text-indigo-700"
                    onClick={handleClearAll}
                    disabled={selected.size === 0 || mutation.isPending}
                  >
                    Clear
                  </button>
                </div>
              </div>
              <label className="relative block text-sm text-slate-500">
                <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
                  <Search className="h-4 w-4" aria-hidden="true" />
                </span>
                <input
                  type="search"
                  className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm text-slate-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-blue-500 sm:w-64"
                  placeholder="Filter agents"
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                  disabled={mutation.isPending}
                />
              </label>
            </div>

            <div className="max-h-96 overflow-y-auto rounded-lg border border-slate-200">
              {filteredAgents.length === 0 ? (
                <div className="px-4 py-6 text-sm text-slate-500">No agents match your filter.</div>
              ) : (
                <ul className="divide-y divide-slate-200">
                  {filteredAgents.map((agent) => (
                    <li key={agent.id}>
                      <label className="flex cursor-pointer items-start gap-3 px-4 py-3 hover:bg-slate-50">
                        <input
                          type="checkbox"
                          className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                          checked={selected.has(agent.id)}
                          onChange={() => handleToggle(agent.id)}
                          disabled={mutation.isPending}
                        />
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
                          {agent.lastInteractionAt && (
                            <p className="text-[11px] uppercase tracking-wide text-slate-400">
                              Last interaction: {formatTimestamp(agent.lastInteractionAt)}
                            </p>
                          )}
                        </div>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </form>
    </Modal>
  )
}

function useFilteredAgents(data: McpServerAssignmentResponse | undefined, searchTerm: string): McpServerAssignmentAgent[] {
  return useMemo(() => {
    if (!data) {
      return []
    }
    if (!searchTerm.trim()) {
      return data.agents
    }
    const query = searchTerm.trim().toLowerCase()
    return data.agents.filter((agent) => {
      const name = agent.name.toLowerCase()
      const description = (agent.description || '').toLowerCase()
      return name.includes(query) || description.includes(query)
    })
  }, [data, searchTerm])
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

function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return 'Unknown'
  }
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
