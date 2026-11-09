import { useEffect, useRef, useState } from 'react'
import { ArrowUp } from 'lucide-react'
import { api, streamChat, type Message, type Scope, type Source } from '@/api'
import { AnswerText } from '@/components/AnswerText'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'

const SCOPES: [Scope, string][] = [
  ['all', 'GDPR + my documents'],
  ['gdpr', 'GDPR'],
  ['docs', 'My documents'],
]

const EXAMPLES = [
  'Can a company keep my data forever?',
  'When does a business need a data protection officer?',
  'How fast must a data breach be reported?',
]

export function ChatView({
  chatId,
  scope,
  onScope,
  onChatSaved,
  onCite,
}: {
  chatId: number | null
  scope: Scope
  onScope: (scope: Scope) => void
  onChatSaved: (id: number) => void
  onCite: (source: Source) => void
}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const justCreated = useRef<number | null>(null) // chat created by this view mid-stream: don't reload it
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (chatId === null) {
      setMessages([])
      return
    }
    if (chatId === justCreated.current) return
    setLoadError(null)
    api
      .chat(chatId)
      .then((c) => setMessages(c.messages))
      .catch((e: Error) => setLoadError(e.message))
  }, [chatId])

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  async function send(question: string) {
    question = question.trim()
    if (!question || busy) return
    setInput('')
    setBusy(true)
    setMessages((m) => [...m, { role: 'user', content: question }, { role: 'assistant', content: '', pending: true }])
    const update = (fn: (m: Message) => Message) =>
      setMessages((m) => [...m.slice(0, -1), fn(m[m.length - 1])])
    try {
      await streamChat(question, chatId, scope, {
        chat: (id) => {
          justCreated.current = id
          onChatSaved(id)
        },
        sources: (sources) => update((a) => ({ ...a, sources })),
        token: (t) => update((a) => ({ ...a, content: a.content + t })),
        done: (r) => update((a) => ({ ...a, content: r.answer, citations: r.citations, pending: false })),
        error: (error) => update((a) => ({ ...a, pending: false, error })),
      })
    } catch (e) {
      update((a) => ({ ...a, pending: false, error: (e as Error).message }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-2xl px-6 py-10" aria-live="polite">
          {loadError && <p className="text-sm">Could not load this chat: {loadError}</p>}
          {messages.length === 0 && !loadError && (
            <div className="pt-[12vh]">
              <h1 className="text-2xl font-medium tracking-tight">Ask about the GDPR</h1>
              <p className="mt-2 text-muted-foreground">
                Answers come only from the regulation's text and your documents, with every claim linked to its source.
              </p>
              <div className="mt-8 flex flex-col items-start gap-1">
                {EXAMPLES.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => send(q)}
                    className="cursor-pointer py-1 text-left underline-offset-4 hover:underline"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="space-y-8">
            {messages.map((m, i) => (
              <MessageView key={i} message={m} onCite={onCite} />
            ))}
          </div>
          <div ref={bottom} />
        </div>
      </div>
      <form
        className="border-t"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
        <div className="mx-auto w-full max-w-2xl px-6 pt-3">
          <ToggleGroup
            type="single"
            value={scope}
            onValueChange={(v) => v && onScope(v as Scope)}
            aria-label="Search in"
            spacing={0}
            className="border"
          >
            {SCOPES.map(([value, label]) => (
              <ToggleGroupItem
                key={value}
                value={value}
                size="sm"
                className="px-3 text-xs font-normal text-muted-foreground data-[state=on]:bg-foreground data-[state=on]:text-background"
              >
                {label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <div className="mx-auto flex w-full max-w-2xl items-end gap-2 px-6 pt-3">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send(input)
              }
            }}
            placeholder="Ask a question about the GDPR"
            aria-label="Question"
            maxLength={2000}
            rows={1}
            className="max-h-40 min-h-10 resize-none shadow-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          <Button type="submit" size="icon-lg" className="size-10" disabled={busy || !input.trim()} aria-label="Send">
            <ArrowUp />
          </Button>
        </div>
        <p className="mx-auto max-w-2xl px-6 pt-2 pb-4 text-xs text-muted-foreground">
          Generated from the GDPR text and can be wrong. Not legal advice.
        </p>
      </form>
    </div>
  )
}

function MessageView({ message: m, onCite }: { message: Message; onCite: (s: Source) => void }) {
  if (m.role === 'user') return <p className="font-medium">{m.content}</p>
  const sources = m.sources ?? []
  const waiting = m.pending && (!m.content || 'NOT_FOUND'.startsWith(m.content.trim().slice(0, 9)))
  if (m.error)
    return (
      <p className="border-l-2 border-foreground pl-4">
        Something went wrong: {m.error}
        {/api key/i.test(m.error) && ' Add your OpenRouter key in Settings.'}
      </p>
    )
  if (waiting)
    return (
      <p className="text-muted-foreground" role="status">
        {sources.length ? 'Writing the answer…' : 'Searching…'}
      </p>
    )
  const cited = (m.citations ?? []).map((cid) => sources.find((s) => s.cid === cid)).filter((s) => s !== undefined)
  return (
    <div>
      <AnswerText text={m.content} sources={sources} onCite={onCite} />
      {cited.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t pt-3 text-sm text-muted-foreground">
          <span>Sources</span>
          {cited.map((s) => (
            <button
              key={s.cid}
              type="button"
              onClick={() => onCite(s)}
              className="cursor-pointer text-foreground underline decoration-[#6e6e73] underline-offset-4 hover:decoration-[#171717]"
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
