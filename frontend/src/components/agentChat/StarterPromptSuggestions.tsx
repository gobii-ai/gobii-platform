import { memo } from 'react'

export type StarterPrompt = {
  id: string
  text: string
  category: 'capabilities' | 'deliverables' | 'integrations' | 'planning'
}

export const STARTER_PROMPT_POOL: StarterPrompt[] = [
  { id: 'capabilities-overview', text: 'What cool things can you do?', category: 'capabilities' },
  { id: 'daily-workflow', text: 'What tasks can you automate for me this week?', category: 'capabilities' },
  { id: 'proactive-monitor', text: 'What should you monitor for me proactively?', category: 'capabilities' },
  { id: 'send-pdf-csv', text: 'Can you send a PDF or CSV to my email?', category: 'deliverables' },
  { id: 'meeting-brief', text: 'Draft a one-page brief for my next team meeting.', category: 'deliverables' },
  { id: 'research-summary', text: 'Summarize the top trends in my industry this month.', category: 'deliverables' },
  { id: 'email-digest', text: 'Can you prepare a concise daily email digest for me?', category: 'integrations' },
  { id: 'chart-generation', text: 'Can you generate charts from my data and explain the trends?', category: 'deliverables' },
  { id: 'file-upload-analysis', text: 'If I upload a file, can you analyze it and summarize key takeaways?', category: 'capabilities' },
  { id: 'weekly-plan', text: 'Build me a focused weekly plan with priorities.', category: 'planning' },
  { id: 'follow-up-plan', text: 'What follow-ups should I do today?', category: 'planning' },
  { id: 'risk-scan', text: 'What risks should I pay attention to right now?', category: 'planning' },
]

function shuffle<T>(items: T[]): T[] {
  const copy = items.slice()
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1))
    const temp = copy[i]
    copy[i] = copy[j]
    copy[j] = temp
  }
  return copy
}

export function selectStarterPrompts(pool: StarterPrompt[], targetCount = 5): StarterPrompt[] {
  if (targetCount <= 0 || pool.length === 0) {
    return []
  }

  const categoryOrder: StarterPrompt['category'][] = ['capabilities', 'deliverables', 'integrations', 'planning']
  const buckets = shuffle(categoryOrder).map((category) =>
    shuffle(pool.filter((prompt) => prompt.category === category)),
  )

  const selected: StarterPrompt[] = []
  for (const bucket of buckets) {
    if (selected.length >= targetCount) {
      break
    }
    const first = bucket.shift()
    if (first) {
      selected.push(first)
    }
  }

  if (selected.length >= targetCount) {
    return selected.slice(0, targetCount)
  }

  const remaining = shuffle(buckets.flat())
  return [...selected, ...remaining.slice(0, targetCount - selected.length)]
}

type StarterPromptSuggestionsProps = {
  prompts: StarterPrompt[]
  disabled?: boolean
  onSelect?: (prompt: StarterPrompt, position: number) => void | Promise<void>
}

export const StarterPromptSuggestions = memo(function StarterPromptSuggestions({
  prompts,
  disabled = false,
  onSelect,
}: StarterPromptSuggestionsProps) {
  if (!prompts.length) {
    return null
  }

  return (
    <div className="flex flex-wrap gap-1.5" aria-label="Starter prompts">
      {prompts.map((prompt, index) => (
        <button
          key={prompt.id}
          type="button"
          disabled={disabled}
          onClick={() => {
            void onSelect?.(prompt, index)
          }}
          className="inline-flex rounded-full bg-slate-100 px-2.5 py-1 text-left text-xs font-medium leading-5 text-slate-700 transition hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {prompt.text}
        </button>
      ))}
    </div>
  )
})
