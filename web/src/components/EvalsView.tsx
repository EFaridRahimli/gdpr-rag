import { useEffect, useState } from 'react'
import { api, type Evals } from '@/api'

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
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl space-y-12 px-6 py-10">
        <div>
          <h1 className="text-2xl font-medium tracking-tight">Evaluation</h1>
          <p className="mt-2 text-muted-foreground">
            50 synthetic, auto-filtered questions, each written from one GDPR paragraph. Scores are
            measured, not estimated.
          </p>
        </div>
        {error && <p className="text-sm">Could not load results: {error}</p>}

        {a && (
          <section className="space-y-3">
            <h2 className="font-medium">Answers</h2>
            <Table
              head={['Answered', 'Expected article cited', 'Faithful', 'Citations correct', 'Off-topic declined']}
              rows={[[
                pct(a.answered),
                pct(a.target_cited),
                pct(a.faithful),
                pct(a.citations_ok),
                a.declined_off_topic === undefined ? '-' : pct(a.declined_off_topic),
              ]]}
            />
            <p className="text-sm text-muted-foreground">
              Answers by GPT-6.1-Sol on a ChatGPT plan. Faithfulness and citation correctness are judged by
              Claude Sonnet, a different model family. Off-topic: 10 questions the GDPR can’t answer, which
              should be declined.
            </p>
          </section>
        )}

        {evals?.compliance && (
          <section className="space-y-3">
            <h2 className="font-medium">Compliance check</h2>
            <Table
              head={['Planted gaps found', 'Covered items wrongly flagged']}
              rows={[[
                `${Math.round(evals.compliance.gaps_found * evals.compliance.planted_gaps)} of ${evals.compliance.planted_gaps}`,
                `${Math.round(evals.compliance.false_alarms * evals.compliance.covered_items)} of ${evals.compliance.covered_items}`,
              ]]}
            />
            <p className="text-sm text-muted-foreground">
              Three synthetic privacy policies with deliberately planted gaps against the Art. 13/14 checklist.
            </p>
          </section>
        )}

        {evals?.retrieval && (
          <section className="space-y-3">
            <h2 className="font-medium">Retrieval</h2>
            <Table
              head={['Mode', 'Embedding model', 'Recall@5', 'Recall@10', 'MRR']}
              rows={evals.retrieval.map((r) => [
                r.mode === DEFAULT.mode && r.embed_model === DEFAULT.embed_model ? `${r.mode} (in use)` : r.mode,
                r.embed_model,
                pct(r['recall@5']),
                pct(r['recall@10']),
                pct(r.mrr),
              ])}
            />
          </section>
        )}
      </div>
    </div>
  )
}

function Table({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm tabular-nums">
        <thead className="text-muted-foreground">
          <tr className="border-b">
            {head.map((h) => (
              <th key={h} scope="col" className="py-2 pr-6 font-normal">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={`border-b ${r[0].endsWith('(in use)') ? 'font-medium' : ''}`}>
              {r.map((c, j) => (
                <td key={j} className="py-2 pr-6 whitespace-nowrap">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
