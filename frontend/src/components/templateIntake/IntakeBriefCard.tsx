import { useEffect, useMemo, useRef, useState } from 'react'
import './intakeBriefCard.css'

/*
 * Template intake — the "focus stage" living card (design locked 2026-08-03).
 *
 * Schema-driven: renders any template's intake_schema. Question kinds split by
 * who can know the answer:
 *  - text  (sample): ghost example; untouched -> assumed, agent confirms in chat
 *  - tags  (capture): rapid-add, user's words verbatim; empty -> agent asks
 *  - choice (template-known options): honest defaults, preselected
 * Every question is skippable ("later"), and the whole card is answerable by
 * pressing Next/Enter straight through.
 */

export type IntakeChoiceOption = { t: string; d?: string; rec?: boolean; other?: boolean }
export type IntakeQuestion = {
  id: string
  eyebrow: string
  q: string
  type: 'text' | 'tags' | 'choice'
  sample?: string
  ph?: string
  options?: IntakeChoiceOption[]
  default?: number
}
export type IntakeSchema = {
  templateName: string
  accent: string
  pasteLabel?: string
  questions: IntakeQuestion[]
}

const SKIP = '__skip__'

function ChatGlyph() {
  return (
    <svg className="tiq-chatglyph" width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function DelegateButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="tiq-delegate" onClick={onClick}>
      <ChatGlyph />
      My agent will ask me — sort it out in chat
    </button>
  )
}

type AnswerValue = string | string[]

export function IntakeBriefCard({
  schema,
  onSubmit,
}: {
  schema: IntakeSchema
  onSubmit: (answers: Record<string, AnswerValue>) => void
}) {
  const questions = schema.questions
  const [active, setActive] = useState(0)
  const [done, setDone] = useState<boolean[]>(() => questions.map(() => false))
  const [answers, setAnswers] = useState<Record<string, AnswerValue>>({})
  const [assumed, setAssumed] = useState<Record<string, boolean>>({})
  const [textDraft, setTextDraft] = useState('')
  const [tagDraft, setTagDraft] = useState('')
  const [otherOpen, setOtherOpen] = useState(false)
  const [otherDraft, setOtherDraft] = useState('')
  const inputRef = useRef<HTMLInputElement | null>(null)

  const allDone = done.every(Boolean)
  const current = questions[active]

  useEffect(() => {
    inputRef.current?.focus()
  }, [active, otherOpen])

  const summaryFor = (q: IntakeQuestion): string => {
    const v = answers[q.id]
    if (v === SKIP) return '→ my agent will ask in chat'
    if (Array.isArray(v)) return v.join(' · ')
    if (typeof v === 'string' && v) return v
    if (q.type === 'text' && q.sample) return `e.g. ${q.sample}`
    if (q.type === 'tags') return '— in your words, or your agent asks in chat'
    if (q.type === 'choice' && q.options) return q.options[q.default ?? 0].t
    return ''
  }

  const nextOpenAfter = (doneList: boolean[], from: number): number => {
    // advance forward first, wrap after — never yank the user backwards
    for (let i = from + 1; i < doneList.length; i++) if (!doneList[i]) return i
    for (let i = 0; i <= from; i++) if (!doneList[i]) return i
    return -1
  }

  const commitRow = (index: number, value: AnswerValue, isAssumed = false) => {
    const q = questions[index]
    const nextDone = [...done]
    nextDone[index] = true
    setAnswers((a) => ({ ...a, [q.id]: value }))
    setAssumed((a) => ({ ...a, [q.id]: isAssumed }))
    setDone(nextDone)
    const open = nextOpenAfter(nextDone, index)
    setActive(open === -1 ? index : open)
    setTextDraft('')
    setTagDraft('')
    setOtherOpen(false)
    setOtherDraft('')
  }

  const currentTags = (): string[] => {
    const v = answers[current.id]
    return Array.isArray(v) ? v : []
  }

  const handleNext = () => {
    if (allDone) {
      onSubmit(answers)
      return
    }
    const q = current
    if (q.type === 'text') {
      const typed = textDraft.trim() || (typeof answers[q.id] === 'string' ? (answers[q.id] as string) : '')
      if (typed) commitRow(active, typed, false)
      else commitRow(active, q.sample ?? '', true)
    } else if (q.type === 'tags') {
      const pending = tagDraft.trim()
      const list = pending
        ? [...currentTags(), ...pending.split(',').map((s) => s.trim()).filter(Boolean)]
        : currentTags()
      commitRow(active, list.length ? list : SKIP, false)
    } else {
      const idx = typeof answers[q.id] === 'string' && answers[q.id] ? -1 : (q.default ?? 0)
      const value = idx === -1 ? (answers[q.id] as string) : q.options![idx].t
      commitRow(active, value, false)
    }
  }

  const jumpTo = (index: number) => {
    if (index === active && !allDone) return
    // Leaving a row that has content marks it done — navigating away must never
    // cost the user their progress or drag them back later.
    const q = current
    const nextDone = [...done]
    if (!allDone && !nextDone[active]) {
      if (q.type === 'text') {
        const typed = textDraft.trim() || (typeof answers[q.id] === 'string' && answers[q.id] !== SKIP ? (answers[q.id] as string) : '')
        if (typed) {
          setAnswers((a) => ({ ...a, [q.id]: typed }))
          setAssumed((a) => ({ ...a, [q.id]: false }))
          nextDone[active] = true
        }
      } else if (q.type === 'tags') {
        const pending = tagDraft.trim()
        const list = pending
          ? [...currentTags(), ...pending.split(',').map((s) => s.trim()).filter(Boolean)]
          : currentTags()
        if (list.length) {
          setAnswers((a) => ({ ...a, [q.id]: list }))
          nextDone[active] = true
        }
      } else if (q.type === 'choice') {
        // A choice row always displays a selection — leaving it accepts what's
        // shown (explicit pick, prior delegation, or the visible default).
        if (answers[q.id] !== SKIP) {
          const value =
            typeof answers[q.id] === 'string' && answers[q.id]
              ? (answers[q.id] as string)
              : q.options![q.default ?? 0].t
          setAnswers((a) => ({ ...a, [q.id]: value }))
        }
        nextDone[active] = true
      }
    }
    nextDone[index] = false
    setDone(nextDone)
    const existing = answers[questions[index].id]
    setTextDraft(typeof existing === 'string' && existing !== SKIP ? existing : '')
    setTagDraft('')
    setOtherOpen(false)
    setActive(index)
  }

  const skipRow = (index: number) => commitRow(index, SKIP, false)

  const addTags = (raw: string) => {
    const parts = raw.split(',').map((s) => s.trim()).filter(Boolean)
    if (!parts.length) return
    const q = current
    setAnswers((a) => {
      const prior = Array.isArray(a[q.id]) ? (a[q.id] as string[]) : []
      return { ...a, [q.id]: [...prior, ...parts] }
    })
    setTagDraft('')
  }

  const removeTag = (tagIndex: number) => {
    const q = current
    setAnswers((a) => {
      const prior = Array.isArray(a[q.id]) ? (a[q.id] as string[]) : []
      return { ...a, [q.id]: prior.filter((_, i) => i !== tagIndex) }
    })
  }

  const skipAll = () => {
    const nextAnswers = { ...answers }
    const nextAssumed = { ...assumed }
    questions.forEach((q, i) => {
      if (done[i]) return
      if (q.type === 'text') {
        if (i === active && textDraft.trim()) {
          nextAnswers[q.id] = textDraft.trim()
          nextAssumed[q.id] = false
        } else if (typeof nextAnswers[q.id] !== 'string' || !nextAnswers[q.id]) {
          nextAnswers[q.id] = q.sample ?? ''
          nextAssumed[q.id] = true
        }
      } else if (q.type === 'tags') {
        const prior = Array.isArray(nextAnswers[q.id]) ? (nextAnswers[q.id] as string[]) : []
        nextAnswers[q.id] = prior.length ? prior : SKIP
      } else {
        if (typeof nextAnswers[q.id] !== 'string' || !nextAnswers[q.id]) {
          nextAnswers[q.id] = q.options![q.default ?? 0].t
        }
      }
    })
    setAnswers(nextAnswers)
    setAssumed(nextAssumed)
    setDone(questions.map(() => true))
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Enter') return
      const target = e.target as HTMLElement
      if (target.classList.contains('tiq-taginput') || target.classList.contains('tiq-otherinput')) return
      e.preventDefault()
      handleNext()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })

  const activeBody = useMemo(() => {
    if (allDone) return null
    const q = current
    if (q.type === 'text') {
      return (
        <>
          <input
            ref={inputRef}
            className="tiq-input"
            value={textDraft}
            placeholder={q.sample ? `e.g. ${q.sample}` : ''}
            onChange={(e) => setTextDraft(e.target.value)}
          />
          {textDraft.trim() === '' ? <DelegateButton onClick={() => skipRow(active)} /> : null}
        </>
      )
    }
    if (q.type === 'tags') {
      const list = currentTags()
      return (
        <>
          <div className="tiq-chips">
            {list.map((tag, i) => (
              <button key={`${tag}-${i}`} type="button" className="tiq-tag" onClick={() => removeTag(i)}>
                {tag}
                <span className="tiq-x">×</span>
              </button>
            ))}
            <input
              ref={inputRef}
              className="tiq-taginput"
              value={tagDraft}
              placeholder={list.length ? 'add another ↵' : q.ph ?? 'type and press Enter'}
              onChange={(e) => setTagDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  if (tagDraft.trim()) addTags(tagDraft)
                  else handleNext()
                } else if (e.key === 'Backspace' && !tagDraft && list.length) {
                  removeTag(list.length - 1)
                }
              }}
            />
          </div>
          {list.length === 0 && tagDraft.trim() === '' ? <DelegateButton onClick={() => skipRow(active)} /> : null}
        </>
      )
    }
    const selected = typeof answers[q.id] === 'string' && answers[q.id] && answers[q.id] !== SKIP
      ? q.options!.findIndex((o) => o.t === answers[q.id])
      : (q.default ?? 0)
    return (
      <div className="tiq-opts">
        {q.options!.map((opt, i) =>
          opt.other ? (
            otherOpen ? (
              <input
                key="other-input"
                ref={inputRef}
                className="tiq-taginput tiq-otherinput"
                style={{ width: '100%', borderRadius: 13 }}
                value={otherDraft}
                placeholder="describe it in your own words ↵"
                onChange={(e) => setOtherDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    if (otherDraft.trim()) commitRow(active, otherDraft.trim(), false)
                    else setOtherOpen(false)
                  }
                }}
              />
            ) : (
              <button key={opt.t} type="button" className="tiq-opt tiq-other" onClick={() => setOtherOpen(true)}>
                <span className="tiq-key">…</span>
                {opt.t}
              </button>
            )
          ) : (
            <button
              key={opt.t}
              type="button"
              className={`tiq-opt${i === selected ? ' tiq-sel' : ''}`}
              onClick={() => commitRow(active, opt.t, false)}
            >
              {opt.rec ? <span className="tiq-rec">Recommended</span> : null}
              <span className="tiq-key">{i + 1}</span>
              <span>
                <span>{opt.t}</span>
                {opt.d ? <div className="tiq-optd">{opt.d}</div> : null}
              </span>
            </button>
          ),
        )}
        {typeof answers[q.id] === 'string' && answers[q.id] && answers[q.id] !== SKIP ? null : (
          <button type="button" className="tiq-opt tiq-deleg" onClick={() => skipRow(active)}>
            <ChatGlyph />
            <span>
              <span>My agent will ask me</span>
              <div className="tiq-optd">We&rsquo;ll sort this out in chat — nothing is lost.</div>
            </span>
          </button>
        )}
      </div>
    )
  }, [active, allDone, answers, current, otherDraft, otherOpen, tagDraft, textDraft])

  return (
    <div className="tiq-stage">
      <div className="tiq-head">
        <h1>
          Brief your <span className="tiq-accent">{schema.accent}</span>
        </h1>
        <p>Answer in place, or just keep hitting Next — good defaults are already lined up.</p>
      </div>
      <div className={`tiq-card${allDone ? '' : ' tiq-staging'}`}>
        {questions.map((q, i) => {
          const isActive = i === active && !allDone
          return (
            <div key={q.id} className={`tiq-row${done[i] ? ' tiq-done' : ''}${isActive ? ' tiq-active' : ''}`}>
              {!isActive ? (
                <div className="tiq-rowhead" onClick={() => jumpTo(i)}>
                  <span className="tiq-stat">{done[i] ? '✓' : i + 1}</span>
                  <span className="tiq-lbl">{q.eyebrow}</span>
                  <span className="tiq-val">
                    {done[i] ? (
                      <>
                        {summaryFor(q)}
                        {assumed[q.id] ? <span className="tiq-asm">Assumed</span> : null}
                      </>
                    ) : (
                      <span className="tiq-ph">{summaryFor(q)}</span>
                    )}
                  </span>
                </div>
              ) : (
                <div className="tiq-body">
                  <h2 className="tiq-q">{q.q}</h2>
                  {activeBody}
                </div>
              )}
            </div>
          )
        })}
        <div className="tiq-foot">
          <button type="button" className="tiq-skipall tiq-foot-quiet" onClick={skipAll}>
            Use recommended for the rest
          </button>
          <span className="tiq-dots tiq-foot-quiet">
            {questions.map((q, i) => (
              <i key={q.id} className={done[i] ? 'tiq-dot-done' : i === active && !allDone ? 'tiq-dot-cur' : ''} />
            ))}
          </span>
          <button type="button" className="tiq-next" onClick={handleNext}>
            {allDone ? 'Create account & launch' : 'Next'}
            <span className="tiq-kbd">↵</span>
          </button>
        </div>
      </div>
      <p className="tiq-under">
        This brief just gets your agent started — anything you leave open, it asks about in chat.
      </p>
    </div>
  )
}
