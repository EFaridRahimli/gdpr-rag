import { useEffect, useState } from 'react'
import { api, type IndexStatus, type Settings } from '@/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const INDEX_TEXT: Record<IndexStatus['status'], string> = {
  missing: 'The GDPR index builds automatically once a key is saved.',
  building: 'Building the GDPR index… this takes about a minute.',
  ready: 'GDPR index ready.',
  error: 'Building the GDPR index failed.',
}

export function SettingsView({
  settings,
  onChange,
}: {
  settings: Settings | null
  onChange: (settings: Settings) => void
}) {
  const setSettings = onChange
  const [key, setKey] = useState('')
  const [model, setModel] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (settings && !model) setModel(settings.model)
  }, [settings, model])

  async function retryIndex() {
    try {
      const index = await api.buildIndex()
      if (settings) onChange({ ...settings, index })
    } catch (e) {
      setStatus((e as Error).message)
    }
  }

  async function save(body: { api_key?: string; model?: string }, done: string) {
    setSaving(true)
    setStatus(null)
    try {
      setSettings(await api.saveSettings(body))
      setKey('')
      setStatus(done)
    } catch (e) {
      setStatus((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const keyText = !settings?.key_hint
    ? 'No key yet. Answers need an OpenRouter key.'
    : `Using ${settings.key_hint}, ${settings.key_source === 'settings' ? 'saved here' : 'from .env'}.`

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-xl space-y-12 px-6 py-10">
        <h1 className="text-2xl font-medium tracking-tight">Settings</h1>

        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            save({ api_key: key }, 'Key saved.')
          }}
        >
          <Label htmlFor="key">OpenRouter API key</Label>
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
              onClick={() => save({ api_key: '' }, 'Saved key removed.')}
            >
              Remove saved key
            </Button>
          )}
        </form>

        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            save({ model }, 'Model saved.')
          }}
        >
          <Label htmlFor="model">Answer model</Label>
          <p className="text-sm text-muted-foreground">
            Any OpenRouter model id. Evaluated with anthropic/claude-haiku-5.5.
          </p>
          <div className="flex gap-2">
            <Input
              id="model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="shadow-none focus-visible:ring-1 focus-visible:ring-ring"
            />
            <Button type="submit" variant="outline" disabled={saving || !model.trim()}>
              Save
            </Button>
          </div>
        </form>

        <p role="status" className="min-h-6 text-sm">
          {status}
        </p>
      </div>
    </div>
  )
}
