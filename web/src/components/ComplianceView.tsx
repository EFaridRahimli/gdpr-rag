import { useCallback, useEffect, useRef, useState } from 'react'
import { Banner } from '@astryxdesign/core/Banner'
import { Blockquote } from '@astryxdesign/core/Blockquote'
import { Button } from '@astryxdesign/core/Button'
import { Heading } from '@astryxdesign/core/Heading'
import { HStack } from '@astryxdesign/core/HStack'
import { List, ListItem } from '@astryxdesign/core/List'
import { Text } from '@astryxdesign/core/Text'
import { VStack } from '@astryxdesign/core/VStack'
import { api, HttpError, type ComplianceItem, type Report, type Source, type Status } from '@/api'
import { Page } from '@/components/Page'

const ORDER: Status[] = ['Missing', 'Partial', 'Covered', 'Not applicable']
const isGap = (s: Status) => s === 'Missing' || s === 'Partial'

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
    <Page
      title="Transparency check"
      description={`${doc.name} against the information GDPR Articles 13 and 14 require a privacy notice to give.`}
    >
      {busy && (
        <Text as="p" role="status" color="secondary">
          Checking 14 requirements… this takes about 15 seconds.
        </Text>
      )}
      {error && !busy && (
        <Banner
          status="error"
          title="Something went wrong"
          description={error}
          endContent={<Button label="Try again" size="sm" onClick={run} />}
        />
      )}

      {report && !busy && (
        <VStack gap={4}>
          <HStack gap={4} wrap="wrap" hAlign="between" vAlign="center">
            <Text>
              {ORDER.filter((s) => report.counts[s])
                .map((s) => `${report.counts[s]} ${s.toLowerCase()}`)
                .join(' · ')}
            </Text>
            <HStack gap={2}>
              <Button label="Export Markdown" size="sm" onClick={download} />
              <Button label="Run again" variant="ghost" size="sm" onClick={run} />
            </HStack>
          </HStack>

          <List hasDividers edge="flush">
            {sorted(report.items).map((i) => (
              <ListItem
                key={i.id}
                align="start"
                label={
                  <Heading level={4} accessibilityLevel={2}>
                    {i.title}
                  </Heading>
                }
                endContent={
                  <Text weight={isGap(i.status) ? 'semibold' : undefined} color={isGap(i.status) ? 'primary' : 'secondary'}>
                    {i.status}
                  </Text>
                }
                description={
                  <VStack gap={3} hAlign="start">
                    {i.source ? (
                      <Button label={i.article} variant="ghost" size="sm" onClick={() => onCite(i.source!)} />
                    ) : (
                      <Text type="supporting">{i.article}</Text>
                    )}
                    {i.evidence.map((q) => (
                      <Blockquote key={q}>{q}</Blockquote>
                    ))}
                    {i.fix && <Text as="p">Fix: {i.fix}</Text>}
                    {i.note && <Text type="supporting">{i.note}</Text>}
                  </VStack>
                }
              />
            ))}
          </List>
          <Text type="supporting">
            Checked {report.created_at.slice(0, 16).replace('T', ' ')}. Quotes are verified against the document;
            statuses are a model's judgement. Not legal advice.
          </Text>
        </VStack>
      )}
    </Page>
  )
}
