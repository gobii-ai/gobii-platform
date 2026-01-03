import type { ChangeEvent, FormEvent, KeyboardEvent } from 'react'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ArrowUp, Paperclip, X } from 'lucide-react'

type AgentComposerProps = {
  onSubmit?: (message: string, attachments?: File[]) => void | Promise<void>
  disabled?: boolean
}

export function AgentComposer({ onSubmit, disabled = false }: AgentComposerProps) {
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState<File[]>([])
  const [isSending, setIsSending] = useState(false)
  const [isDragActive, setIsDragActive] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const shellRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const attachmentInputId = useId()
  const dragCounter = useRef(0)

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
    if ((!trimmed && attachments.length === 0) || disabled || isSending) {
      return
    }
    const attachmentsSnapshot = attachments.slice()
    if (onSubmit) {
      try {
        setIsSending(true)
        setBody('')
        setAttachments([])
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
        requestAnimationFrame(() => adjustTextareaHeight(true))
        await onSubmit(trimmed, attachmentsSnapshot)
      } finally {
        setIsSending(false)
      }
    } else {
      setBody('')
      setAttachments([])
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
      requestAnimationFrame(() => adjustTextareaHeight(true))
    }
  }, [adjustTextareaHeight, attachments, body, disabled, isSending, onSubmit])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    await submitMessage()
  }

  const handleKeyDown = async (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) {
      return
    }
    const shouldSend = (event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey
    if (!shouldSend) {
      return
    }
    event.preventDefault()
    await submitMessage()
  }

  const addAttachments = useCallback((files: File[]) => {
    if (disabled || isSending) {
      return
    }
    if (!files.length) {
      return
    }
    setAttachments((current) => [...current, ...files])
  }, [disabled, isSending])

  const handleAttachmentChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    addAttachments(files)
    event.target.value = ''
  }, [addAttachments])

  const removeAttachment = useCallback((index: number) => {
    setAttachments((current) => current.filter((_, currentIndex) => currentIndex !== index))
  }, [])

  useEffect(() => {
    const hasFiles = (event: DragEvent) => {
      const types = Array.from(event.dataTransfer?.types ?? [])
      return types.includes('Files')
    }

    const handleDragEnter = (event: DragEvent) => {
      if (disabled || isSending || !hasFiles(event)) {
        return
      }
      event.preventDefault()
      dragCounter.current += 1
      setIsDragActive(true)
    }

    const handleDragOver = (event: DragEvent) => {
      if (disabled || isSending || !hasFiles(event)) {
        return
      }
      event.preventDefault()
    }

    const handleDragLeave = (event: DragEvent) => {
      if (!hasFiles(event)) {
        return
      }
      event.preventDefault()
      dragCounter.current = Math.max(0, dragCounter.current - 1)
      if (dragCounter.current === 0) {
        setIsDragActive(false)
      }
    }

    const handleDrop = (event: DragEvent) => {
      if (disabled || isSending || !hasFiles(event)) {
        return
      }
      event.preventDefault()
      dragCounter.current = 0
      setIsDragActive(false)
      const files = Array.from(event.dataTransfer?.files ?? [])
      addAttachments(files)
    }

    window.addEventListener('dragenter', handleDragEnter)
    window.addEventListener('dragover', handleDragOver)
    window.addEventListener('dragleave', handleDragLeave)
    window.addEventListener('drop', handleDrop)

    return () => {
      window.removeEventListener('dragenter', handleDragEnter)
      window.removeEventListener('dragover', handleDragOver)
      window.removeEventListener('dragleave', handleDragLeave)
      window.removeEventListener('drop', handleDrop)
    }
  }, [addAttachments, disabled, isSending])

  return (
    <div className="composer-shell" id="agent-composer-shell" ref={shellRef}>
      <div className="composer-surface">
        <form className="flex flex-col" onSubmit={handleSubmit}>
          {isDragActive ? (
            <div className="agent-chat-drop-overlay" aria-hidden="true">
              <div className="agent-chat-drop-overlay__panel">Drop files to upload</div>
            </div>
          ) : null}
          <div className="composer-input-surface flex flex-col gap-2 rounded-2xl border border-slate-200/70 bg-white px-4 py-3 transition">
            <div className="flex items-center gap-3">
              <input
                ref={fileInputRef}
                id={attachmentInputId}
                type="file"
                className="sr-only"
                multiple
                disabled={disabled || isSending}
                onChange={handleAttachmentChange}
              />
              <label
                htmlFor={attachmentInputId}
                className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-slate-200/70 text-slate-500 transition hover:border-slate-300 hover:text-slate-700"
                aria-label="Attach file"
                title="Attach file"
              >
                <Paperclip className="h-4 w-4" aria-hidden="true" />
              </label>
              <textarea
                name="body"
                rows={1}
                required={attachments.length === 0}
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
                disabled={disabled || isSending || (!body.trim() && attachments.length === 0)}
                title={isSending ? 'Sending' : 'Send (Cmd/Ctrl+Enter)'}
                aria-label={isSending ? 'Sending message' : 'Send message (Cmd/Ctrl+Enter)'}
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
            {attachments.length > 0 ? (
              <div className="flex flex-wrap gap-2 text-xs text-slate-600">
                {attachments.map((file, index) => (
                  <span
                    key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
                    className="inline-flex max-w-full items-center gap-2 rounded-full border border-slate-200/70 px-3 py-1"
                  >
                    <span className="max-w-[160px] truncate" title={file.name}>
                      {file.name}
                    </span>
                    <button
                      type="button"
                      className="inline-flex items-center justify-center text-slate-400 transition hover:text-slate-600"
                      onClick={() => removeAttachment(index)}
                      disabled={disabled || isSending}
                      aria-label={`Remove ${file.name}`}
                    >
                      <X className="h-3 w-3" aria-hidden="true" />
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
            <p className="composer-shortcut-hint">Cmd/Ctrl+Enter to send</p>
          </div>
        </form>
      </div>
    </div>
  )
}
