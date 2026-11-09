export type Source = {
  cid: string
  id: number
  source: string
  label: string
  ref: string
  heading: string
  text: string
}

export type Message = {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[] | null
  citations?: string[] | null
  pending?: boolean
  error?: string
}

export type ChatSummary = { id: number; title: string; created_at: string }
export type Chat = ChatSummary & { messages: Message[] }
export type IndexStatus = { status: 'missing' | 'building' | 'ready' | 'error'; error: string | null }
export type Settings = {
  key_hint: string | null
  key_source: 'settings' | 'env' | null
  model: string
  fast_model: string
  embed_model: string
  index: IndexStatus
  chatgpt: { connected: boolean; email: string | null; embeddings: 'yes' | 'no' | null }
}
export type PlanModel = { slug: string; name: string }
export type RetrievalRow = {
  mode: string
  embed_model: string
  'recall@5': number
  'recall@10': number
  mrr: number
}
export type AnswerSummary = {
  answered: number
  target_cited: number
  faithful: number
  citations_ok: number
  declined_off_topic?: number
}
export type ComplianceSummary = { gaps_found: number; false_alarms: number; planted_gaps: number; covered_items: number }
export type Evals = { retrieval: RetrievalRow[] | null; answers: AnswerSummary | null; compliance: ComplianceSummary | null }
export type Done = { answer: string; citations: string[]; found: boolean }
export type Scope = 'all' | 'gdpr' | 'docs'
export type Doc = { id: number; name: string; size: number; chunks: number; created_at: string }
export type Status = 'Covered' | 'Partial' | 'Missing' | 'Not applicable'
export type ComplianceItem = {
  id: string
  title: string
  article: string
  status: Status
  evidence: string[]
  fix: string
  note: string | null
  source?: Source
}
export type Report = { doc_id: number; items: ComplianceItem[]; counts: Record<Status, number>; created_at: string }

export class HttpError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.json().catch(() => null)
    throw new HttpError(typeof body?.detail === 'string' ? body.detail : `${r.status} ${r.statusText}`, r.status)
  }
  return (r.status === 204 ? undefined : await r.json()) as T
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export const api = {
  chats: () => fetch('/api/chats').then(json<ChatSummary[]>),
  chat: (id: number, signal?: AbortSignal) => fetch(`/api/chats/${id}`, { signal }).then(json<Chat>),
  deleteChat: (id: number) => fetch(`/api/chats/${id}`, { method: 'DELETE' }).then(json<void>),
  settings: () => fetch('/api/settings').then(json<Settings>),
  saveSettings: (body: { api_key?: string; model?: string; fast_model?: string }) =>
    fetch('/api/settings', { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify(body) }).then(
      json<Settings>,
    ),
  evals: () => fetch('/api/evals').then(json<Evals>),
  buildIndex: () => fetch('/api/index', { method: 'POST' }).then(json<IndexStatus>),
  chatgptStart: () => fetch('/api/chatgpt/start', { method: 'POST' }).then(json<{ url: string }>),
  chatgptModels: (signal?: AbortSignal) => fetch('/api/chatgpt/models', { signal }).then(json<PlanModel[]>),
  chatgptSignOut: () => fetch('/api/chatgpt/signout', { method: 'POST' }).then(json<Settings>),
  documents: () => fetch('/api/documents').then(json<Doc[]>),
  uploadDocument: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return fetch('/api/documents', { method: 'POST', body }).then(json<Doc>)
  },
  deleteDocument: (id: number) => fetch(`/api/documents/${id}`, { method: 'DELETE' }).then(json<void>),
  compliance: (id: number, signal?: AbortSignal) =>
    fetch(`/api/documents/${id}/compliance`, { signal }).then(json<Report>),
  runCompliance: (id: number) => fetch(`/api/documents/${id}/compliance`, { method: 'POST' }).then(json<Report>),
}

type Handlers = {
  chat: (id: number) => void
  sources: (sources: Source[]) => void
  token: (text: string) => void
  done: (result: Done) => void
  error: (message: string) => void
}

/** POST a question and dispatch the server-sent events (EventSource can't POST). */
export async function streamChat(
  question: string,
  chatId: number | null,
  scope: Scope,
  on: Handlers,
  signal?: AbortSignal,
) {
  const r = await fetch('/api/chat', {
    signal,
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ question, chat_id: chatId, scope }),
  })
  if (!r.ok || !r.body) return on.error((await json<never>(r).catch((e: Error) => e)).message)
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  let finished = false
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let end
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const event = block.match(/^event: (.*)$/m)?.[1]
      const data = block.match(/^data: (.*)$/m)?.[1]
      if (!event || data === undefined) continue
      const payload = JSON.parse(data)
      if (event === 'chat') on.chat(payload.id)
      else if (event === 'sources') on.sources(payload)
      else if (event === 'token') on.token(payload)
      else if (event === 'done') {
        finished = true
        on.done(payload)
      } else if (event === 'error') {
        finished = true
        on.error(payload.message)
      }
    }
  }
  if (!finished) on.error('The connection was lost before the answer finished. Try again.')
}
