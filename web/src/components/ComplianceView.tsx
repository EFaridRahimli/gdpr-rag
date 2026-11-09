import { useCallback, useEffect, useRef, useState } from 'react'
import { api, HttpError, type ComplianceItem, type Report, type Source, type Status } from '@/api'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const ORDER: Status[] = ['Missing', 'Partial', 'Covered', 'Not applicable']

function toMarkdown(name: string, report: Report) {
  const lines = [
    `# GDPR transparency check: ${name}`,
    '',
    `Checked ${report.created_at} against GDPR Articles 13 and 14. Not legal advice.`,
    '',
    ORDER.map((s) => `${s}: ${report.counts[s]}`).join(' · '),
    '',
  ]
  for (const i of sorted(report.items)) {
    lines.push(`## ${i.status}: ${i.title} (${i.article})`, '')
    for (const q of i.evidence) lines.push(`> ${q}`, '')
    if (i.fix) lines.push(`Fix: ${i.fix}`, '')
    if (i.note) lines.push(`Note: ${i.note}`, '')
  }
  return lines.join('\n')
}

const sorted = (items: ComplianceItem[]) =>
  [...items].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status))

export function ComplianceView({
  doc,
  onCite,
}: {
  doc: { id: number; name: string }
  onCite: (source: Source) => void
}) {
  const [report, setReport] = useState<Report | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const alive = useRef(true) // App remounts this view per document; ignore late replies after leaving
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const run = useCallback(() => {
    setBusy(true)
    setError(null)
    api
      .runCompliance(doc.id)
      .then((r) => alive.current && setReport(r))
      .catch((e: Error) => alive.current && setError(e.message))
      .finally(() => alive.current && setBusy(false))
  }, [doc.id])

  useEffect(() => {
    const load = new AbortController() // also stops StrictMode's double mount starting two paid checks
    api
      .compliance(doc.id, load.signal)
      .then(setReport)
      .catch((e: Error) => {
        if (load.signal.aborted) return
        if (e instanceof HttpError && e.status === 404) run() // no saved report yet: run the check
        else setError(`Could not load the saved report: ${e.message}`)
      })
    return () => load.abort()
  }, [doc.id, run])

  function download() {
    if (!report) return
    const url = URL.createObjectURL(new Blob([toMarkdown(doc.name, report)], { type: 'text/markdown' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: `${doc.name} - GDPR check.md` })
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-6 py-10">
        <h1 className="text-2xl font-medium tracking-tight">Transparency check</h1>
        <p className="mt-2 text-muted-foreground">
          {doc.name} against the information GDPR Articles 13 and 14 require a privacy notice to give.
        </p>

        {busy && (
          <p role="status" className="mt-10 text-muted-foreground">
            Checking 14 requirements… this takes about 15 seconds.
          </p>
        )}
        {error && !busy && (
          <div className="mt-10 space-y-3">
            <p className="border-l-2 border-foreground pl-4">Something went wrong: {error}</p>
            <Button variant="outline" onClick={run}>
              Try again
            </Button>
          </div>
        )}

        {report && !busy && (
          <>
            <div className="mt-8 flex flex-wrap items-center justify-between gap-4 border-y py-4">
              <p className="text-sm">
                {ORDER.filter((s) => report.counts[s]).map((s, n) => (
                  <span key={s}>
                    {n > 0 && <span className="text-muted-foreground"> · </span>}
                    <span className={s === 'Missing' || s === 'Partial' ? 'font-semibold' : ''}>{report.counts[s]}</span>{' '}
                    {s.toLowerCase()}
                  </span>
                ))}
              </p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={download}>
                  Export Markdown
                </Button>
                <Button variant="ghost" size="sm" onClick={run}>
                  Run again
                </Button>
              </div>
            </div>

            <ol className="divide-y">
              {sorted(report.items).map((i) => (
                <li key={i.id} className="py-6">
                  <div className="flex items-baseline justify-between gap-6">
                    <h2 className="font-medium">{i.title}</h2>
                    <span
                      className={cn(
                        'shrink-0 text-xs tracking-wide uppercase',
                        i.status === 'Missing' || i.status === 'Partial' ? 'font-semibold' : 'text-muted-foreground',
                      )}
                    >
                      {i.status}
                    </span>
                  </div>
                  {i.source ? (
                    <button
                      type="button"
                      onClick={() => onCite(i.source!)}
                      className="mt-1 cursor-pointer text-sm text-muted-foreground underline decoration-[#6e6e73] underline-offset-4 hover:text-foreground"
                    >
                      {i.article}
                    </button>
                  ) : (
                    <p className="mt-1 text-sm text-muted-foreground">{i.article}</p>
                  )}
                  {i.evidence.map((q) => (
                    <blockquote key={q} className="mt-3 border-l pl-4 text-[15px] leading-7 text-muted-foreground">
                      {q}
                    </blockquote>
                  ))}
                  {i.fix && <p className="mt-3 text-[15px] leading-7">Fix: {i.fix}</p>}
                  {i.note && <p className="mt-2 text-sm text-muted-foreground">{i.note}</p>}
                </li>
              ))}
            </ol>
            <p className="mt-6 text-xs text-muted-foreground">
              Checked {report.created_at.slice(0, 16).replace('T', ' ')}. Quotes are verified against the document;
              statuses are a model's judgement. Not legal advice.
            </p>
          </>
        )}
      </div>
    </div>
  )
}
