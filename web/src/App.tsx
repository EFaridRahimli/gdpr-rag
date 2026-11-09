import { useCallback, useEffect, useState } from 'react'
import { Menu, Plus, Trash2, X } from 'lucide-react'
import { api, type ChatSummary, type Scope, type Settings, type Source } from '@/api'
import { ChatView } from '@/components/ChatView'
import { ComplianceView } from '@/components/ComplianceView'
import { DocumentsView } from '@/components/DocumentsView'
import { EvalsView } from '@/components/EvalsView'
import { SettingsView } from '@/components/SettingsView'
import { SourcePanel } from '@/components/SourcePanel'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type View =
  | { kind: 'chat'; id: number | null }
  | { kind: 'compliance'; doc: { id: number; name: string } }
  | { kind: 'documents' } | { kind: 'settings' } | { kind: 'evals' }
const PAGES = { documents: 'Documents', evals: 'Evaluation', settings: 'Settings' } as const

// the ChatGPT sign-in returns to /?view=settings&chatgpt=connected (or &chatgpt_error=...)
const returned = new URLSearchParams(window.location.search)
const SIGN_IN_NOTICE = returned.get('chatgpt_error')
  ? `ChatGPT sign-in failed: ${returned.get('chatgpt_error')}`
  : returned.get('chatgpt') === 'connected'
    ? 'Signed in with ChatGPT.'
    : null
if (returned.has('view')) window.history.replaceState(null, '', '/')

export default function App() {
  const [view, setView] = useState<View>(
    returned.get('view') === 'settings' ? { kind: 'settings' } : { kind: 'chat', id: null },
  )
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [source, setSource] = useState<Source | null>(null)
  const [scope, setScope] = useState<Scope>('all')
  const [setup, setSetup] = useState<Settings | null>(null) // key + GDPR index status
  const [offline, setOffline] = useState(false) // the local server isn't answering
  const [navError, setNavError] = useState<string | null>(null)
  const [chatSession, setChatSession] = useState(0) // a fresh ChatView for every chat the user opens

  const refreshSetup = useCallback(() => {
    api
      .settings()
      .then((s) => {
        setSetup(s)
        setOffline(false)
      })
      .catch(() => setOffline(true))
  }, [])
  useEffect(() => {
    refreshSetup()
  }, [refreshSetup])
  useEffect(() => {
    if (setup?.index.status !== 'building') return
    const t = setTimeout(refreshSetup, 3000) // poll until the background build finishes
    return () => clearTimeout(t)
  }, [setup, refreshSetup])
  const [navOpen, setNavOpen] = useState(false) // small screens only

  const refreshChats = useCallback(() => {
    api
      .chats()
      .then((c) => {
        setChats(c)
        setNavError(null)
      })
      .catch((e: Error) => setNavError(`Couldn't load chats: ${e.message}`)) // keep the old list
  }, [])
  useEffect(() => {
    refreshChats()
  }, [refreshChats])

  const go = (v: View) => {
    if (v.kind === 'chat') setChatSession((n) => n + 1)
    refreshSetup() // also notices a server that stopped while the page was open
    setView(v)
    setSource(null)
    setNavOpen(false)
  }
  const closeSource = useCallback(() => setSource(null), [])
  const activeChat = view.kind === 'chat' ? view.id : undefined

  async function remove(id: number) {
    try {
      await api.deleteChat(id)
    } catch (e) {
      return setNavError(`Couldn't delete the chat: ${(e as Error).message}`)
    }
    if (activeChat === id) go({ kind: 'chat', id: null })
    refreshChats()
  }

  return (
    <div className="flex h-dvh flex-col md:flex-row">
      <header className="flex items-center justify-between border-b px-4 py-3 md:hidden">
        <span className="font-medium">GDPR RAG</span>
        <Button variant="ghost" size="icon" onClick={() => setNavOpen(!navOpen)} aria-label="Menu" aria-expanded={navOpen}>
          {navOpen ? <X /> : <Menu />}
        </Button>
      </header>

      <nav
        aria-label="Chats and pages"
        className={cn(
          'flex-col border-r bg-background md:flex md:w-64 md:shrink-0',
          navOpen ? 'fixed inset-x-0 top-[57px] bottom-0 z-20 flex' : 'hidden',
        )}
      >
        <div className="hidden px-5 pt-6 pb-4 font-medium md:block">GDPR RAG</div>
        <div className="px-3 pt-3 md:pt-0">
          <Button variant="outline" className="w-full justify-start shadow-none" onClick={() => go({ kind: 'chat', id: null })}>
            <Plus /> New chat
          </Button>
        </div>
        <ul className="mt-4 flex-1 overflow-y-auto px-3">
          {chats.map((c) => (
            <li key={c.id} className="group flex items-center">
              <button
                type="button"
                onClick={() => go({ kind: 'chat', id: c.id })}
                aria-current={activeChat === c.id ? 'page' : undefined}
                className={cn(
                  'flex-1 cursor-pointer truncate border-l-2 border-transparent px-3 py-2 text-left text-sm hover:bg-muted',
                  activeChat === c.id ? 'border-foreground font-medium' : 'text-muted-foreground',
                )}
              >
                {c.title}
              </button>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => remove(c.id)}
                aria-label={`Delete chat: ${c.title}`}
                className="md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100"
              >
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
        {navError && (
          <p role="alert" className="px-5 py-2 text-sm">
            {navError}
          </p>
        )}
        <div className="space-y-1 border-t px-3 py-3 text-sm">
          {(Object.keys(PAGES) as (keyof typeof PAGES)[]).map((kind) => (
            <button
              key={kind}
              type="button"
              onClick={() => go({ kind })}
              aria-current={view.kind === kind ? 'page' : undefined}
              className={cn(
                'block w-full cursor-pointer border-l-2 border-transparent px-3 py-2 text-left hover:bg-muted',
                view.kind === kind || (kind === 'documents' && view.kind === 'compliance')
                  ? 'border-foreground font-medium'
                  : 'text-muted-foreground',
              )}
            >
              {PAGES[kind]}
            </button>
          ))}
        </div>
      </nav>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {offline && (
          <div role="alert" className="flex items-center justify-between gap-4 border-b px-6 py-3 text-sm">
            <span>Can't reach the gdpr-rag server. Is the Terminal window that runs it still open?</span>
            <Button variant="outline" size="sm" onClick={refreshSetup}>
              Retry
            </Button>
          </div>
        )}
        <div className="flex min-h-0 flex-1">
        {view.kind === 'chat' && (
          <ChatView
            key={chatSession}
            chatId={view.id}
            setup={setup}
            onOpenSettings={() => go({ kind: 'settings' })}
            scope={scope}
            onScope={setScope}
            onChatSaved={(id) => {
              setView({ kind: 'chat', id })
              refreshChats()
            }}
            onCite={setSource}
          />
        )}
        {view.kind === 'documents' && <DocumentsView onCheck={(doc) => go({ kind: 'compliance', doc })} />}
        {view.kind === 'compliance' && <ComplianceView key={view.doc.id} doc={view.doc} onCite={setSource} />}
        {view.kind === 'settings' && <SettingsView settings={setup} onChange={setSetup} notice={SIGN_IN_NOTICE} />}
        {view.kind === 'evals' && <EvalsView />}
        {source && <SourcePanel source={source} onClose={closeSource} />}
        </div>
      </main>
    </div>
  )
}
