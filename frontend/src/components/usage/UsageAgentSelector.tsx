import { useId, useMemo } from 'react'
import {
  Button,
  Dialog,
  DialogTrigger,
  ListBox,
  ListBoxItem,
  Popover,
  type Key,
  type Selection,
} from 'react-aria-components'

import type { UsageAgent } from './types'
import type { UsageStatus } from './store'

type UsageAgentSelectorProps = {
  agents: UsageAgent[]
  status: UsageStatus
  errorMessage: string | null
  selectedAgentIds: Set<string>
  onSelectionChange: (ids: Set<string>) => void
  variant?: 'default' | 'condensed'
}

export function UsageAgentSelector({
  agents,
  status,
  errorMessage,
  selectedAgentIds,
  onSelectionChange,
  variant = 'default',
}: UsageAgentSelectorProps) {
  const isLoading = status === 'loading'
  const isErrored = status === 'error'

  const selectedKeys = useMemo(() => new Set<Key>(Array.from(selectedAgentIds)), [selectedAgentIds])
  const labelId = useId()

  const containerClasses = 'flex flex-col gap-1'
  const buttonClassName = `${
    'flex w-full items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60'
  } ${variant === 'condensed' ? 'sm:w-auto' : ''}`.trim()

  const selectionLabel = useMemo(() => {
    if (selectedAgentIds.size === 0) {
      return 'All agents'
    }
    if (selectedAgentIds.size === 1) {
      const id = Array.from(selectedAgentIds)[0]
      const agent = agents.find((item) => item.id === id)
      return agent ? agent.name : '1 agent selected'
    }
    return `${selectedAgentIds.size} agents selected`
  }, [agents, selectedAgentIds])

  const handleSelectionChange = (keys: Selection) => {
    if (keys === 'all') {
      onSelectionChange(new Set<string>())
      return
    }
    const next = new Set<string>()
    for (const key of keys as Set<Key>) {
      next.add(String(key))
    }
    onSelectionChange(next)
  }

  return (
    <div className={containerClasses}>
      <div
        id={labelId}
        className={variant === 'condensed' ? 'sr-only' : 'text-xs font-semibold uppercase tracking-wide text-slate-500'}
      >
        Agents
      </div>
      <DialogTrigger>
        <Button
          aria-labelledby={labelId}
          className={buttonClassName}
          isDisabled={isLoading || isErrored || agents.length === 0}
        >
          <span>{selectionLabel}</span>
          <span aria-hidden="true">▾</span>
        </Button>
        <Popover className="z-50 mt-2 min-w-56 rounded-xl border border-slate-200 bg-white shadow-xl">
          <Dialog className="p-1">
            <ListBox
              selectionMode="multiple"
              selectionBehavior="toggle"
              selectedKeys={selectedKeys as unknown as Selection}
              onSelectionChange={(keys) => handleSelectionChange(keys as Selection)}
              className="max-h-64 overflow-y-auto p-1 text-sm text-slate-700"
            >
              {agents.map((item) => (
                <ListBoxItem
                  key={item.id}
                  id={item.id}
                  textValue={item.name}
                  className="cursor-pointer rounded-md px-3 py-2 data-[focused]:bg-blue-50 data-[focused]:text-blue-700 data-[selected]:bg-blue-600 data-[selected]:text-white"
                >
                  {item.name}
                </ListBoxItem>
              ))}
            </ListBox>
          </Dialog>
        </Popover>
      </DialogTrigger>
      {isLoading ? (
        <span className="text-xs text-slate-400">Loading agents…</span>
      ) : null}
      {isErrored && errorMessage ? (
        <span className="text-xs text-red-600">{errorMessage}</span>
      ) : null}
    </div>
  )
}

export type { UsageAgentSelectorProps }
