import { useEffect, useState } from 'react'
import { Button } from '@astryxdesign/core/Button'
import { Grid } from '@astryxdesign/core/Grid'
import { Heading } from '@astryxdesign/core/Heading'
import { HStack } from '@astryxdesign/core/HStack'
import { Selector } from '@astryxdesign/core/Selector'
import { StackItem } from '@astryxdesign/core/Stack'
import { Text } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'
import { VStack } from '@astryxdesign/core/VStack'
import { api, type IndexStatus, type PlanModel, type Settings } from '@/api'
import { Page } from '@/components/Page'

const INDEX_TEXT: Record<IndexStatus['status'], string> = {
  missing: 'The GDPR index builds automatically once a key is saved.',
  building: 'Building the GDPR index… this takes about a minute.',
  ready: 'GDPR index ready.',
  error: 'Building the GDPR index failed.',
}

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

  const saveKey = () => {
    if (key.trim() && !saving) run(() => api.saveSettings({ api_key: key }), 'Key saved.')
  }
  const options = planModels.map((m) => ({ value: m.slug, label: m.name }))
  const planEmbeds = settings?.chatgpt.embeddings === 'yes'
  const keyOptional = signedIn && planEmbeds
  const keyText = !settings?.key_hint
    ? keyOptional
      ? 'Not needed: your ChatGPT plan covers search too.'
      : 'Needed for search. ChatGPT plans don’t include the embeddings search uses; indexing costs about $0.03, then fractions of a cent per question.'
    : `Using ${settings.key_hint}, ${settings.key_source === 'settings' ? 'saved here' : 'from .env'}.`

  return (
    <Page title="Settings" width={600}>
      <VStack gap={3} as="section" hAlign="start">
        <Heading level={2}>ChatGPT plan</Heading>
        {!signedIn ? (
          <>
            <Text as="p" color="secondary">
              Required: answers and compliance checks run on your ChatGPT Plus or Pro plan. Signing in happens on
              OpenAI’s site; this app never sees your password.
            </Text>
            <Button label="Sign in with ChatGPT" variant="primary" onClick={signIn} isDisabled={saving} />
          </>
        ) : (
          <>
            <Text as="p" color="secondary">
              Signed in as {settings?.chatgpt.email ?? 'your ChatGPT account'}. Questions and compliance checks use
              your plan.{' '}
              {planEmbeds
                ? 'Search runs on your plan too, so no API key is needed.'
                : 'Search still needs an OpenRouter key (below).'}
            </Text>
            <Grid columns={{ minWidth: 240 }} gap={3} width="100%">
              <Selector
                label="Answer model"
                options={options}
                value={settings?.model}
                isDisabled={saving || !options.length}
                onChange={(v) => run(() => api.saveSettings({ model: v }), 'Answer model saved.')}
                width="100%"
              />
              <Selector
                label="Fast model (search ranking, follow-ups)"
                options={options}
                value={settings?.fast_model}
                isDisabled={saving || !options.length}
                onChange={(v) => run(() => api.saveSettings({ fast_model: v }), 'Fast model saved.')}
                width="100%"
              />
            </Grid>
            <Button
              label="Sign out of ChatGPT"
              variant="ghost"
              isDisabled={saving}
              onClick={() => run(api.chatgptSignOut, 'Signed out of ChatGPT.')}
            />
          </>
        )}
      </VStack>

      <VStack gap={3} as="section" hAlign="start">
        <Heading level={2}>Search</Heading>
        {settings && (
          <HStack gap={2} vAlign="center" wrap="wrap">
            <Text role="status">
              {INDEX_TEXT[settings.index.status]}
              {settings.index.error && ` ${settings.index.error}`}
            </Text>
            {settings.index.status === 'error' && (
              <Button label="Try again" variant="ghost" size="sm" onClick={retryIndex} />
            )}
          </HStack>
        )}
        <HStack gap={2} vAlign="end" width="100%">
          <StackItem size="fill">
          <TextInput
            label={`OpenRouter API key${keyOptional ? '' : ', for search'}`}
            isOptional={keyOptional}
            description={`${keyText} The key stays on this computer and is only sent to OpenRouter.`}
            type="password"
            autoComplete="off"
            value={key}
            onChange={setKey}
            onEnter={saveKey}
            placeholder="sk-or-v1-…"
            width="100%"
          />
          </StackItem>
          <StackItem>
            <Button label="Save" variant="primary" isDisabled={saving || !key.trim()} onClick={saveKey} />
          </StackItem>
        </HStack>
        {settings?.key_source === 'settings' && (
          <Button
            label="Remove saved key"
            variant="ghost"
            onClick={() => run(() => api.saveSettings({ api_key: '' }), 'Saved key removed.')}
          />
        )}
      </VStack>

      <Text as="p" role="status">
        {status}
      </Text>
    </Page>
  )
}
