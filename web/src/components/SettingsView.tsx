import { useEffect, useState } from 'react'
import { api, type IndexStatus, type PlanModel, type Settings } from '@/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const INDEX_TEXT: Record<IndexStatus['status'], string> = {
  missing: 'The GDPR index builds automatically once a key is saved.',
  building: 'Building the GDPR index… this takes about a minute.',
  ready: 'GDPR index ready.',
  error: 'Building the GDPR index failed.',
}
const SELECT =
  'h-9 w-full border border-input bg-background px-3 text-sm focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-none'

export function SettingsView({
  settings,
  onChange,
  notice,
}: {
  settings: Settings | null
  onChange: (settings: Settings) => void
  notice?: string | null // message from the ChatGPT sign-in redirect
}) {
  const [key, setKey] = useState('')
  const [status, setStatus] = useState<string | null>(notice ?? null)
  const [saving, setSaving] = useState(false)
  const [planModels, setPlanModels] = useState<PlanModel[]>([])
  const signedIn = settings?.chatgpt.connected ?? false

  useEffect(() => {
    if (!signedIn) return
    const load = new AbortController()
    api
      .chatgptModels(load.signal)
      .then(setPlanModels)
      .catch((e: Error) => !load.signal.aborted && setStatus(e.message))
    return () => load.abort()
  }, [signedIn])

  async function run(action: () => Promise<Settings>, done: string) {
    setSaving(true)
    setStatus(null)
    try {
      onChange(await action())
      setKey('')
      setStatus(done)
    } catch (e) {
      setStatus((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  async function signIn() {
    setSaving(true)
    setStatus(null)
    try {
      window.location.href = (await api.chatgptStart()).url // OpenAI sends the browser back here
    } catch (e) {
      setStatus((e as Error).message)
      setSaving(false)
    }
  }

  async function retryIndex() {
    try {
      const index = await api.buildIndex()
      if (settings) onChange({ ...settings, index })
    } catch (e) {
      setStatus((e as Error).message)
    }
  }

  const planEmbeds = settings?.chatgpt.embeddings === 'yes'
  const keyOptional = signedIn && planEmbeds
  const keyText = !settings?.key_hint
    ? keyOptional
      ? 'Not needed: your ChatGPT plan covers search too.'
      : 'Needed for search. ChatGPT plans don’t include the embeddings search uses; indexing costs about $0.03, then fractions of a cent per question.'
    : `Using ${settings.key_hint}, ${settings.key_source === 'settings' ? 'saved here' : 'from .env'}.`

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-xl space-y-12 px-6 py-10">
        <h1 className="text-2xl font-medium tracking-tight">Settings</h1>

        <section className="space-y-3" aria-labelledby="chatgpt-heading">
          <h2 id="chatgpt-heading" className="text-sm font-medium">
            ChatGPT plan
          </h2>
          {!signedIn ? (
            <>
              <p className="text-sm text-muted-foreground">
                Required: answers and compliance checks run on your ChatGPT Plus or Pro plan. Signing in
                happens on OpenAI’s site; this app never sees your password.
              </p>
              <Button onClick={signIn} disabled={saving}>
                Sign in with ChatGPT
              </Button>
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                Signed in as {settings?.chatgpt.email ?? 'your ChatGPT account'}. Questions and compliance
                checks use your plan.{' '}
                {planEmbeds
                  ? 'Search runs on your plan too, so no API key is needed.'
                  : 'Search still needs an OpenRouter key (below).'}
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor="plan-model">Answer model</Label>
                  <select
                    id="plan-model"
                    className={SELECT}
                    value={settings?.model}
                    disabled={saving || !planModels.length}
                    onChange={(e) => run(() => api.saveSettings({ model: e.target.value }), 'Answer model saved.')}
                  >
                    {planModels.map((m) => (
                      <option key={m.slug} value={m.slug}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="fast-model">Fast model (search ranking, follow-ups)</Label>
                  <select
                    id="fast-model"
                    className={SELECT}
                    value={settings?.fast_model}
                    disabled={saving || !planModels.length}
                    onChange={(e) =>
                      run(() => api.saveSettings({ fast_model: e.target.value }), 'Fast model saved.')
                    }
                  >
                    {planModels.map((m) => (
                      <option key={m.slug} value={m.slug}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <Button
                variant="link"
                className="h-auto px-0 text-muted-foreground"
                disabled={saving}
                onClick={() => run(api.chatgptSignOut, 'Signed out of ChatGPT.')}
              >
                Sign out of ChatGPT
              </Button>
            </>
          )}
        </section>

        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            run(() => api.saveSettings({ api_key: key }), 'Key saved.')
          }}
        >
          <Label htmlFor="key">OpenRouter API key, for search{keyOptional ? ' (optional)' : ''}</Label>
          <p className="text-sm text-muted-foreground">
            {keyText} The key stays on this computer and is only sent to OpenRouter.
          </p>
          {settings && (
            <p role="status" className="text-sm">
              {INDEX_TEXT[settings.index.status]}
              {settings.index.error && <span className="text-muted-foreground"> {settings.index.error}</span>}
              {settings.index.status === 'error' && (
                <Button type="button" variant="link" className="h-auto px-1" onClick={retryIndex}>
                  Try again
                </Button>
              )}
            </p>
          )}
          <div className="flex gap-2">
            <Input
              id="key"
              type="password"
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="sk-or-v1-…"
              className="shadow-none focus-visible:ring-1 focus-visible:ring-ring"
            />
            <Button type="submit" disabled={saving || !key.trim()}>
              Save
            </Button>
          </div>
          {settings?.key_source === 'settings' && (
            <Button
              type="button"
              variant="link"
              className="h-auto px-0 text-muted-foreground"
              onClick={() => run(() => api.saveSettings({ api_key: '' }), 'Saved key removed.')}
            >
              Remove saved key
            </Button>
          )}
        </form>

        <p role="status" className="min-h-6 text-sm">
          {status}
        </p>
      </div>
    </div>
  )
}
