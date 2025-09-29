import type { FormEvent } from 'react'
import { useState } from 'react'

type AgentComposerProps = {
  agentName: string
  onSubmit?: (message: string) => void | Promise<void>
  disabled?: boolean
}

export function AgentComposer({ agentName, onSubmit, disabled = false }: AgentComposerProps) {
  const [body, setBody] = useState('')

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!body.trim() || disabled) {
      return
    }
    if (onSubmit) {
      await onSubmit(body.trim())
    }
    setBody('')
  }

  return (
    <div className="composer-shell" id="agent-composer-shell">
      <div className="composer-surface">
        <form className="flex flex-col" onSubmit={handleSubmit}>
          <div className="composer-input-surface flex flex-col gap-2 rounded-2xl border border-slate-200/70 bg-white px-4 py-3 transition">
            <textarea
              name="body"
              rows={1}
              required
              className="block w-full resize-none border-0 bg-transparent px-0 py-1 text-sm leading-5 text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-0 min-h-[1.8rem]"
              placeholder="Send a message..."
              value={body}
              onChange={(event) => setBody(event.target.value)}
              disabled={disabled}
            />
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-col gap-1.5 text-xs text-slate-500 sm:flex-row sm:items-center sm:gap-3.5">
                <div className="text-[0.75rem] text-slate-500 sm:text-sm">
                  <span className="text-slate-400">To:</span>
                  <span className="ml-1 font-medium text-slate-900">{agentName}</span>
                </div>
              </div>
              <button type="submit" className="composer-send-button" disabled={disabled || !body.trim()}>
                Send
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
