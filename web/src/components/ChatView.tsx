import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Banner } from '@astryxdesign/core/Banner'
import { Button } from '@astryxdesign/core/Button'
import { ChatComposer, ChatLayout, ChatMessage, ChatMessageBubble, ChatMessageList } from '@astryxdesign/core/Chat'
import { Heading } from '@astryxdesign/core/Heading'
import { HStack } from '@astryxdesign/core/HStack'
import { Layout, LayoutContent } from '@astryxdesign/core/Layout'
import { SegmentedControl, SegmentedControlItem } from '@astryxdesign/core/SegmentedControl'
import { Text } from '@astryxdesign/core/Text'
import { VStack } from '@astryxdesign/core/VStack'
import { api, streamChat, type Message, type Scope, type Settings, type Source } from '@/api'
import { AnswerText } from '@/components/AnswerText'

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

const MAX_QUESTION = 2000 // the server rejects longer questions
// from the ai-chat template: the chat fills its column so the composer docks at the bottom
const CHAT_FILL: CSSProperties = { flex: 1, minHeight: 0 }

export function ChatView({
  chatId,
  setup,
  onOpenSettings,
  scope,
  onScope,
  onChatSaved,
  onCite,
}: {
  chatId: number | null
  setup: Settings | null
  onOpenSettings: () => void
  scope: Scope
  onScope: (scope: Scope) => void
  onChatSaved: (id: number) => void
  onCite: (source: Source) => void
}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  // App mounts a fresh ChatView for every chat the user opens, so this only skips reloading the
  // chat this view itself created mid-answer
  const justCreated = useRef<number | null>(null)
  const answering = useRef<AbortController | null>(null)

  useEffect(() => () => answering.current?.abort(), []) // leaving the chat stops its answer

  useEffect(() => {
    if (chatId === null || chatId === justCreated.current) return
    const load = new AbortController()
    setLoadError(null)
    api
      .chat(chatId, load.signal)
      .then((c) => setMessages(c.messages))
      .catch((e: Error) => !load.signal.aborted && setLoadError(e.message))
    return () => load.abort()
  }, [chatId])

  async function send(question: string) {
    question = question.trim()
    if (!question || busy) return
    setInput('')
    setBusy(true)
    setMessages((m) => [...m, { role: 'user', content: question }, { role: 'assistant', content: '', pending: true }])
    const update = (fn: (m: Message) => Message) =>
      setMessages((m) => [...m.slice(0, -1), fn(m[m.length - 1])])
    const controller = new AbortController()
    answering.current = controller
    try {
      await streamChat(
        question,
        chatId,
        scope,
        {
          chat: (id) => {
            justCreated.current = id
            onChatSaved(id)
          },
          sources: (sources) => update((a) => ({ ...a, sources })),
          token: (t) => update((a) => ({ ...a, content: a.content + t })),
          done: (r) => update((a) => ({ ...a, content: r.answer, citations: r.citations, pending: false })),
          error: (error) => update((a) => ({ ...a, pending: false, error })),
        },
        controller.signal,
      )
    } catch (e) {
      if (!controller.signal.aborted) update((a) => ({ ...a, pending: false, error: (e as Error).message }))
    } finally {
      setBusy(false)
    }
  }

  const needsSetup = setup && (!setup.chatgpt.connected || setup.index.status !== 'ready')
  const empty = loadError ? (
    <Banner status="error" title="Could not load this chat" description={loadError} />
  ) : (
    <VStack gap={6}>
      <VStack gap={2}>
        <Heading level={1}>Ask about the GDPR</Heading>
        <Text as="p" color="secondary">
          Answers come only from the regulation's text and your documents, with every claim linked to its source.
        </Text>
      </VStack>
      {needsSetup ? (
        <SetupNotice setup={setup} onOpenSettings={onOpenSettings} />
      ) : (
        <VStack gap={1} hAlign="start">
          {EXAMPLES.map((q) => (
            <Button key={q} label={q} variant="ghost" onClick={() => send(q)} />
          ))}
        </VStack>
      )}
    </VStack>
  )

  return (
    <Layout
      height="fill"
      content={
        <LayoutContent padding={0}>
          <VStack height="100%">
            <ChatLayout
              density="spacious"
              style={CHAT_FILL}
              composer={
                <ChatComposer
                  value={input}
                  onChange={(v) => setInput(v.slice(0, MAX_QUESTION))}
                  onSubmit={send}
                  placeholder="Ask a question about the GDPR"
                  isDisabled={busy}
                  headerActions={
                    <SegmentedControl label="Search in" size="sm" value={scope} onChange={(v) => onScope(v as Scope)}>
                      {SCOPES.map(([value, label]) => (
                        <SegmentedControlItem key={value} value={value} label={label} />
                      ))}
                    </SegmentedControl>
                  }
                  headerContext={<Text type="supporting">Can be wrong. Not legal advice.</Text>}
                />
              }
            >
              <ChatMessageList isStreaming={busy} emptyState={empty}>
                {messages.map((m, i) => (
                  <ChatMessage key={i} sender={m.role}>
                    <MessageBody message={m} onCite={onCite} />
                    {m.role === 'user' && messages[i + 1]?.role !== 'assistant' && !busy && (
                      <ChatMessageBubble variant="ghost">
                        <Text type="supporting">This answer was interrupted and wasn't saved.</Text>
                      </ChatMessageBubble>
                    )}
                  </ChatMessage>
                ))}
              </ChatMessageList>
            </ChatLayout>
          </VStack>
        </LayoutContent>
      }
    />
  )
}

function MessageBody({ message: m, onCite }: { message: Message; onCite: (s: Source) => void }) {
  if (m.role === 'user') return <ChatMessageBubble>{m.content}</ChatMessageBubble>
  const sources = m.sources ?? []
  const waiting = m.pending && (!m.content || 'NOT_FOUND'.startsWith(m.content.trim().slice(0, 9)))
  if (m.error)
    return (
      <ChatMessageBubble variant="ghost">
        <Banner
          status="error"
          title="Something went wrong"
          description={`${m.error}${/api key/i.test(m.error) ? ' Add your OpenRouter key in Settings.' : ''}`}
        />
      </ChatMessageBubble>
    )
  if (waiting)
    return (
      <ChatMessageBubble variant="ghost">
        <Text color="secondary">{sources.length ? 'Writing the answer…' : 'Searching…'}</Text>
      </ChatMessageBubble>
    )
  const cited = (m.citations ?? []).map((cid) => sources.find((s) => s.cid === cid)).filter((s) => s !== undefined)
  return (
    <ChatMessageBubble
      variant="ghost"
      metadata={
        cited.length > 0 && (
          <HStack gap={1} wrap="wrap" vAlign="center">
            <Text type="supporting">Sources</Text>
            {cited.map((s) => (
              <Button key={s.cid} label={s.label} variant="ghost" size="sm" onClick={() => onCite(s)} />
            ))}
          </HStack>
        )
      }
    >
      <AnswerText text={m.content} sources={sources} isStreaming={m.pending} onCite={onCite} />
    </ChatMessageBubble>
  )
}

function SetupNotice({ setup, onOpenSettings }: { setup: Settings; onOpenSettings: () => void }) {
  const steps = [
    !setup.chatgpt.connected && 'sign in with ChatGPT (Plus or Pro) for answers',
    !setup.key_hint && setup.chatgpt.embeddings !== 'yes' && 'add an OpenRouter key for search',
  ].filter(Boolean)
  if (!steps.length && setup.index.status === 'building')
    return (
      <Banner
        status="info"
        title="Building the GDPR index…"
        description="This takes about a minute, then you can start asking."
      />
    )
  return (
    <Banner
      status="info"
      title={
        steps.length
          ? `To get started, ${steps.join(' and ')} in Settings.`
          : 'The GDPR index needs to be built before you can ask questions.'
      }
      endContent={<Button label="Open Settings" variant="primary" size="sm" onClick={onOpenSettings} />}
    />
  )
}
