import { useCallback, useEffect, useState } from 'react'
import { Menu, Plus, Trash2, X } from 'lucide-react'
import { api, type ChatSummary, type Scope, type Source } from '@/api'
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

export default function App() {
  const [view, setView] = useState<View>({ kind: 'chat', id: null })
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [source, setSource] = useState<Source | null>(null)
  const [scope, setScope] = useState<Scope>('all')
  const [navOpen, setNavOpen] = useState(false) // small screens only

  const refreshChats = useCallback(() => {
    api.chats().then(setChats).catch(() => setChats([]))
  }, [])
  useEffect(() => {
    refreshChats()
  }, [refreshChats])

  const go = (v: View) => {
    setView(v)
    setSource(null)
    setNavOpen(false)
  }
  const closeSource = useCallback(() => setSource(null), [])
  const activeChat = view.kind === 'chat' ? view.id : undefined

  async function remove(id: number) {
    await api.deleteChat(id)
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

      <main className="flex min-h-0 min-w-0 flex-1">
        {view.kind === 'chat' && (
          <ChatView
            chatId={view.id}
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
        {view.kind === 'compliance' && <ComplianceView doc={view.doc} onCite={setSource} />}
        {view.kind === 'settings' && <SettingsView />}
        {view.kind === 'evals' && <EvalsView />}
        {source && <SourcePanel source={source} onClose={closeSource} />}
      </main>
    </div>
  )
}
