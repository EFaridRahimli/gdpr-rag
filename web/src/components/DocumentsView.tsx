import { useEffect, useState } from 'react'
import { Button } from '@astryxdesign/core/Button'
import { FileInput } from '@astryxdesign/core/FileInput'
import { HStack } from '@astryxdesign/core/HStack'
import { List, ListItem } from '@astryxdesign/core/List'
import { Text } from '@astryxdesign/core/Text'
import { api, type Doc } from '@/api'
import { Page } from '@/components/Page'

const MAX_BYTES = 20 * 1024 * 1024
const kb = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`)

export function DocumentsView({ onCheck }: { onCheck: (doc: Doc) => void }) {
  const [docs, setDocs] = useState<Doc[]>([])
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = () => {
    api.documents().then(setDocs).catch((e: Error) => setStatus(`Could not load documents: ${e.message}`))
  }
  useEffect(() => {
    refresh()
  }, [])

  async function upload(files: File | File[] | null) {
    const file = Array.isArray(files) ? files[0] : files
    if (!file || busy) return // one upload at a time (a drop can arrive mid-upload)
    if (file.size > MAX_BYTES) return setStatus(`${file.name} is larger than 20 MB.`)
    setBusy(true)
    setStatus(`Reading and indexing ${file.name}…`)
    try {
      const doc = await api.uploadDocument(file)
      setStatus(`${doc.name} added: ${doc.chunks} passages indexed.`)
      refresh()
    } catch (e) {
      setStatus(`${file.name}: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  async function remove(doc: Doc) {
    try {
      await api.deleteDocument(doc.id)
      setStatus(`${doc.name} deleted.`)
      refresh()
    } catch (e) {
      setStatus((e as Error).message)
    }
  }

  return (
    <Page
      title="Documents"
      description="Add your own documents, such as a privacy policy, and ask about them alongside the GDPR."
    >
      <FileInput
        label="Upload a document"
        mode="dropzone"
        value={null} // each pick uploads at once; the list below shows what's stored
        onChange={upload}
        accept=".pdf,.docx,.md,.txt"
        isLoading={busy}
        isDisabled={busy}
        placeholder={busy ? 'Indexing…' : 'Choose a file or drop it here'}
        description="PDF with selectable text, DOCX, Markdown or plain text, up to 20 MB. The text is sent to OpenRouter to be embedded and, when relevant, to answer questions."
      />

      <Text as="p" role="status">
        {status}
      </Text>

      {docs.length > 0 && (
        <List hasDividers edge="flush">
          {docs.map((d) => (
            <ListItem
              key={d.id}
              label={d.name}
              description={`${d.chunks} passages · ${kb(d.size)} · added ${d.created_at.slice(0, 10)}`}
              endContent={
                <HStack gap={2}>
                  <Button label="Check compliance" variant="primary" size="sm" onClick={() => onCheck(d)} />
                  <Button label={`Delete ${d.name}`} size="sm" onClick={() => remove(d)}>
                    Delete
                  </Button>
                </HStack>
              }
            />
          ))}
        </List>
      )}
    </Page>
  )
}
