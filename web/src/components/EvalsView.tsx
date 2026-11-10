import { useEffect, useState } from 'react'
import { Banner } from '@astryxdesign/core/Banner'
import { Heading } from '@astryxdesign/core/Heading'
import { Table } from '@astryxdesign/core/Table'
import { Text } from '@astryxdesign/core/Text'
import { VStack } from '@astryxdesign/core/VStack'
import { api, type Evals } from '@/api'
import { Page } from '@/components/Page'

const pct = (x: number) => x.toFixed(2)
const DEFAULT = { mode: 'llm-rerank', embed_model: 'google/gemini-embedding-2' }

export function EvalsView() {
  const [evals, setEvals] = useState<Evals | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.evals().then(setEvals).catch((e: Error) => setError(e.message))
  }, [])

  const a = evals?.answers
  return (
    <Page
      title="Evaluation"
      width={960}
      description="50 synthetic, auto-filtered questions, each written from one GDPR paragraph. Scores are measured, not estimated."
    >
      {error && <Banner status="error" title="Could not load results" description={error} />}

      {a && (
        <Result
          title="Answers"
          head={['Answered', 'Expected article cited', 'Faithful', 'Citations correct', 'Off-topic declined']}
          rows={[[
            pct(a.answered),
            pct(a.target_cited),
            pct(a.faithful),
            pct(a.citations_ok),
            a.declined_off_topic === undefined ? '-' : pct(a.declined_off_topic),
          ]]}
          note="Answers by GPT-6.1-Sol on a ChatGPT plan. Faithfulness and citation correctness are judged by Claude Sonnet, a different model family. Off-topic: 10 questions the GDPR can’t answer, which should be declined."
        />
      )}

      {evals?.compliance && (
        <Result
          title="Compliance check"
          head={['Planted gaps found', 'Covered items wrongly flagged']}
          rows={[[
            `${Math.round(evals.compliance.gaps_found * evals.compliance.planted_gaps)} of ${evals.compliance.planted_gaps}`,
            `${Math.round(evals.compliance.false_alarms * evals.compliance.covered_items)} of ${evals.compliance.covered_items}`,
          ]]}
          note="Three synthetic privacy policies with deliberately planted gaps against the Art. 13/14 checklist."
        />
      )}

      {evals?.retrieval && (
        <Result
          title="Retrieval"
          head={['Mode', 'Embedding model', 'Recall@5', 'Recall@10', 'MRR']}
          rows={evals.retrieval.map((r) => [
            r.mode === DEFAULT.mode && r.embed_model === DEFAULT.embed_model ? `${r.mode} (in use)` : r.mode,
            r.embed_model,
            pct(r['recall@5']),
            pct(r['recall@10']),
            pct(r.mrr),
          ])}
        />
      )}
    </Page>
  )
}

/** One eval section: a heading, a table of string cells and an optional note. */
function Result({ title, head, rows, note }: { title: string; head: string[]; rows: string[][]; note?: string }) {
  const columns = head.map((header, j) => ({ key: String(j), header }))
  const data = rows.map((r, i) => ({ id: i, ...Object.fromEntries(r.map((c, j) => [String(j), c])) }))
  return (
    <VStack gap={3} as="section">
      <Heading level={2}>{title}</Heading>
      <Table data={data} columns={columns} idKey="id" density="compact" />
      {note && <Text type="supporting">{note}</Text>}
    </VStack>
  )
}
