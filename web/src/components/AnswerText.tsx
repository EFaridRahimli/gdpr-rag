import Markdown from 'react-markdown'
import type { Source } from '@/api'

/** Model output as Markdown (raw HTML is not rendered), with [cN] tags turned into source links. */
export function AnswerText({
  text,
  sources,
  onCite,
}: {
  text: string
  sources: Source[]
  onCite: (source: Source) => void
}) {
  const byCid = new Map(sources.map((s) => [s.cid, s]))
  const md = text.replace(/\[(c\d+)\]/g, (_, cid: string) => {
    const s = byCid.get(cid)
    return s ? ` [${s.label}](#${cid})` : ''
  })
  return (
    <div className="space-y-3 leading-7 [&_li]:my-1 [&_ol]:list-decimal [&_ol]:pl-5 [&_strong]:font-semibold [&_ul]:list-disc [&_ul]:pl-5">
      <Markdown
        components={{
          a: ({ href, children }) => {
            const source = href?.startsWith('#c') ? byCid.get(href.slice(1)) : undefined
            if (!source)
              return (
                <a href={href} target="_blank" rel="noreferrer" className="underline underline-offset-4">
                  {children}
                </a>
              )
            return (
              <button
                type="button"
                onClick={() => onCite(source)}
                className="cursor-pointer text-[0.92em] whitespace-nowrap underline decoration-[#6e6e73] underline-offset-4 hover:decoration-[#171717] focus-visible:outline-2"
              >
                {children}
              </button>
            )
          },
        }}
      >
        {md}
      </Markdown>
    </div>
  )
}
