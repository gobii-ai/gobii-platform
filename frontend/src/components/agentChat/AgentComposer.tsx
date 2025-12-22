import type { FormEvent, KeyboardEvent } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowUp } from 'lucide-react'

type AgentComposerProps = {
  onSubmit?: (message: string) => void | Promise<void>
  disabled?: boolean
}

export function AgentComposer({ onSubmit, disabled = false }: AgentComposerProps) {
  const [body, setBody] = useState('')
  const [isSending, setIsSending] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const shellRef = useRef<HTMLDivElement | null>(null)

  const MAX_COMPOSER_HEIGHT = 320

  const adjustTextareaHeight = useCallback(
    (reset = false) => {
      const node = textareaRef.current
      if (!node) return
      if (reset) {
        node.style.height = ''
      }
      node.style.height = 'auto'
      const nextHeight = Math.min(node.scrollHeight, MAX_COMPOSER_HEIGHT)
      node.style.height = `${nextHeight}px`
      node.style.overflowY = node.scrollHeight > MAX_COMPOSER_HEIGHT ? 'auto' : 'hidden'
    },
    [MAX_COMPOSER_HEIGHT],
  )

  useEffect(() => {
    adjustTextareaHeight()
  }, [body, adjustTextareaHeight])

  useEffect(() => {
    adjustTextareaHeight(true)
  }, [adjustTextareaHeight])

  useEffect(() => {
    const node = shellRef.current
    if (!node || typeof window === 'undefined') return

    const updateComposerHeight = () => {
      const height = node.getBoundingClientRect().height
      document.documentElement.style.setProperty('--composer-height', `${height}px`)
      const jumpButton = document.getElementById('jump-to-latest')
      if (jumpButton) {
        jumpButton.style.setProperty('--composer-height', `${height}px`)
      }
    }

    updateComposerHeight()

    const observer = new ResizeObserver(updateComposerHeight)
    observer.observe(node)

    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty('--composer-height')
      const jumpButton = document.getElementById('jump-to-latest')
      if (jumpButton) {
        jumpButton.style.removeProperty('--composer-height')
      }
    }
  }, [])

  const submitMessage = useCallback(async () => {
    const trimmed = body.trim()
    if (!trimmed || disabled || isSending) {
      return
    }
    if (onSubmit) {
      try {
        setIsSending(true)
        await onSubmit(trimmed)
      } finally {
        setIsSending(false)
      }
    }
    setBody('')
    requestAnimationFrame(() => adjustTextareaHeight(true))
  }, [adjustTextareaHeight, body, disabled, isSending, onSubmit])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    await submitMessage()
  }

  const handleKeyDown = async (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const isPlainEnter =
      event.key === 'Enter' && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey
    if (!isPlainEnter || event.nativeEvent.isComposing) {
      return
    }
    event.preventDefault()
    await submitMessage()
  }

  return (
    <div className="composer-shell" id="agent-composer-shell" ref={shellRef}>
      <div className="composer-surface">
        <form className="flex flex-col" onSubmit={handleSubmit}>
          <div className="composer-input-surface flex items-center gap-3 rounded-2xl border border-slate-200/70 bg-white px-4 py-3 transition">
            <textarea
              name="body"
              rows={1}
              required
              className="block min-h-[1.8rem] w-full flex-1 resize-none border-0 bg-transparent px-0 py-1 text-sm leading-5 text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-0"
              placeholder="Send a message..."
              value={body}
              onChange={(event) => setBody(event.target.value)}
              onKeyDown={handleKeyDown}
              disabled={disabled}
              ref={textareaRef}
            />
            <button
              type="submit"
              className="composer-send-button"
              disabled={disabled || isSending || !body.trim()}
              title={isSending ? 'Sending' : 'Send'}
              aria-label={isSending ? 'Sending message' : 'Send message'}
            >
              {isSending ? (
                <span className="inline-flex items-center justify-center">
                  <span
                    className="h-4 w-4 animate-spin rounded-full border-2 border-white/60 border-t-white"
                    aria-hidden="true"
                  />
                  <span className="sr-only">Sending</span>
                </span>
              ) : (
                <>
                  <ArrowUp className="h-4 w-4" aria-hidden="true" />
                  <span className="sr-only">Send</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
