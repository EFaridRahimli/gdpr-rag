import { useCallback, useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { AppShell } from '@astryxdesign/core/AppShell'
import { Banner } from '@astryxdesign/core/Banner'
import { Button } from '@astryxdesign/core/Button'
import { Icon } from '@astryxdesign/core/Icon'
import { SideNav, SideNavHeading, SideNavItem, SideNavSection } from '@astryxdesign/core/SideNav'
import { Text } from '@astryxdesign/core/Text'
import { api, type ChatSummary, type Scope, type Settings, type Source } from '@/api'
import { ChatView } from '@/components/ChatView'
import { ComplianceView } from '@/components/ComplianceView'
import { DocumentsView } from '@/components/DocumentsView'
import { EvalsView } from '@/components/EvalsView'
import { SettingsView } from '@/components/SettingsView'
import { SourcePanel } from '@/components/SourcePanel'

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
  const [source, setSource] = useState<Source | null>(null) // kept while the drawer animates closed
  const [sourceOpen, setSourceOpen] = useState(false)
  const [scope, setScope] = useState<Scope>('all')
  const [setup, setSetup] = useState<Settings | null>(null) // key + GDPR index status
  const [offline, setOffline] = useState(false) // the local server isn't answering
  const [navError, setNavError] = useState<string | null>(null)
  const [chatSession, setChatSession] = useState(0) // a fresh ChatView for every chat the user opens
  const [navOpen, setNavOpen] = useState(false) // small screens only

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
    setSourceOpen(false)
    setNavOpen(false)
  }
  const cite = useCallback((s: Source) => {
    setSource(s)
    setSourceOpen(true)
  }, [])
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

  const sideNav = (
    <SideNav
      header={<SideNavHeading heading="GDPR RAG" />}
      topContent={
        <Button label="New chat" icon={<Icon icon={Plus} />} width="100%" onClick={() => go({ kind: 'chat', id: null })} />
      }
      footer={
        <SideNavSection title="Pages" isHeaderHidden>
          {(Object.keys(PAGES) as (keyof typeof PAGES)[]).map((kind) => (
            <SideNavItem
              key={kind}
              label={PAGES[kind]}
              isSelected={view.kind === kind || (kind === 'documents' && view.kind === 'compliance')}
              onClick={() => go({ kind })}
            />
          ))}
        </SideNavSection>
      }
    >
      <SideNavSection title="Chats">
        {chats.map((c) => (
          <SideNavItem
            key={c.id}
            label={c.title}
            isSelected={activeChat === c.id}
            onClick={() => go({ kind: 'chat', id: c.id })}
            actions={
              <Button
                label={`Delete chat: ${c.title}`}
                icon={<Icon icon={Trash2} />}
                isIconOnly
                variant="ghost"
                size="sm"
                onClick={() => remove(c.id)}
              />
            }
          />
        ))}
      </SideNavSection>
      {navError && (
        <Text as="p" role="alert" color="secondary">
          {navError}
        </Text>
      )}
    </SideNav>
  )

  return (
    <AppShell
      sideNav={sideNav}
      variant="section"
      mobileNav={{ isOpen: navOpen, onOpenChange: setNavOpen }}
      banner={
        offline && (
          <Banner
            status="warning"
            container="section"
            title="Can't reach the gdpr-rag server."
            description="Is the Terminal window that runs it still open?"
            endContent={<Button label="Retry" size="sm" onClick={refreshSetup} />}
          />
        )
      }
    >
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
          onCite={cite}
        />
      )}
      {view.kind === 'documents' && <DocumentsView onCheck={(doc) => go({ kind: 'compliance', doc })} />}
      {view.kind === 'compliance' && <ComplianceView key={view.doc.id} doc={view.doc} onCite={cite} />}
      {view.kind === 'settings' && <SettingsView settings={setup} onChange={setSetup} notice={SIGN_IN_NOTICE} />}
      {view.kind === 'evals' && <EvalsView />}
      {source && <SourcePanel source={source} isOpen={sourceOpen} onOpenChange={setSourceOpen} />}
    </AppShell>
  )
}
