import { useEffect, useRef, useState } from 'react'
import { api, type Doc } from '@/api'
import { Button } from '@/components/ui/button'

const MAX_BYTES = 20 * 1024 * 1024
const kb = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`)

export function DocumentsView({ onCheck }: { onCheck: (doc: Doc) => void }) {
  const [docs, setDocs] = useState<Doc[]>([])
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const refresh = () => {
    api.documents().then(setDocs).catch((e: Error) => setStatus(`Could not load documents: ${e.message}`))
  }
  useEffect(() => {
    refresh()
  }, [])

  async function upload(files: FileList | null) {
    const file = files?.[0]
    if (!file) return
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
      if (input.current) input.current.value = ''
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
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl space-y-10 px-6 py-10">
        <div>
          <h1 className="text-2xl font-medium tracking-tight">Documents</h1>
          <p className="mt-2 text-muted-foreground">
            Add your own documents, such as a privacy policy, and ask about them alongside the GDPR.
          </p>
        </div>

        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            upload(e.dataTransfer.files)
          }}
          className={`border px-6 py-8 ${dragging ? 'border-foreground bg-muted' : ''}`}
        >
          <input
            ref={input}
            type="file"
            accept=".pdf,.docx,.md,.txt"
            className="sr-only"
            id="upload"
            onChange={(e) => upload(e.target.files)}
            disabled={busy}
          />
          <Button onClick={() => input.current?.click()} disabled={busy}>
            {busy ? 'Indexing…' : 'Upload a document'}
          </Button>
          <p className="mt-3 text-sm text-muted-foreground">
            Or drop a file here. PDF with selectable text, DOCX, Markdown or plain text, up to 20 MB.
            The text is sent to OpenRouter to be embedded and, when relevant, to answer questions.
          </p>
        </div>

        <p role="status" className="min-h-6 text-sm">
          {status}
        </p>

        {docs.length > 0 && (
          <ul className="border-t">
            {docs.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-4 border-b py-3">
                <div className="min-w-0">
                  <p className="truncate">{d.name}</p>
                  <p className="text-sm text-muted-foreground">
                    {d.chunks} passages · {kb(d.size)} · added {d.created_at.slice(0, 10)}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button size="sm" onClick={() => onCheck(d)}>
                    Check compliance
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => remove(d)} aria-label={`Delete ${d.name}`}>
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
