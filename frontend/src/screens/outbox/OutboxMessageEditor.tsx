import { useMemo, type FormEvent } from 'react'

import { sanitizeHtml } from '../../util/sanitize'


type OutboxMessageEditorProps = {
  initialHtml: string
  disabled?: boolean
  onChange: (html: string) => void
}

export function OutboxMessageEditor({
  initialHtml,
  disabled = false,
  onChange,
}: OutboxMessageEditorProps) {
  const sanitizedInitialHtml = useMemo(() => sanitizeHtml(initialHtml), [initialHtml])

  const handleInput = (event: FormEvent<HTMLDivElement>) => {
    onChange(sanitizeHtml(event.currentTarget.innerHTML))
  }

  return (
    <div className="grid gap-1">
      <span id="outbox-message-body-label" className="text-xs text-slate-400">Message body</span>
      <div
        contentEditable={!disabled}
        suppressContentEditableWarning
        role="textbox"
        aria-labelledby="outbox-message-body-label"
        aria-multiline="true"
        aria-disabled={disabled}
        onInput={handleInput}
        onClick={(event) => {
          if ((event.target as HTMLElement).closest('a')) {
            event.preventDefault()
          }
        }}
        dangerouslySetInnerHTML={{ __html: sanitizedInitialHtml }}
        className="min-h-80 rounded-lg border border-slate-700 bg-white px-4 py-3 text-sm leading-6 text-slate-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 aria-disabled:cursor-not-allowed aria-disabled:opacity-70 [&_a]:text-blue-600 [&_a]:underline [&_blockquote]:border-l-2 [&_blockquote]:border-slate-300 [&_blockquote]:pl-3 [&_li]:my-1 [&_ol]:list-decimal [&_ol]:pl-6 [&_p]:mb-4 [&_p:last-child]:mb-0 [&_ul]:list-disc [&_ul]:pl-6"
      />
      <span className="text-xs text-slate-500">Edit the message as recipients will see it.</span>
    </div>
  )
}
