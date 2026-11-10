import { Markdown } from '@astryxdesign/core/Markdown'
import type { Source } from '@/api'

// never render images: a poisoned document could otherwise make the browser load a URL
// carrying the conversation (the server's CSP blocks remote images as a second layer)
const NO_IMAGES = { image: () => null }

/** Model output as Markdown, with [cN] tags turned into links that open the cited passage. */
export function AnswerText({
  text,
  sources,
  isStreaming,
  onCite,
}: {
  text: string
  sources: Source[]
  isStreaming?: boolean
  onCite: (source: Source) => void
}) {
  const byCid = new Map(sources.map((s) => [s.cid, s]))
  const md = text.replace(/\[(c\d+)\]/g, (_, cid: string) => {
    const s = byCid.get(cid)
    return s ? ` [${s.label}](#${cid})` : ''
  })
  return (
    <Markdown
      isStreaming={isStreaming}
      components={NO_IMAGES}
      onLinkClick={(href) => {
        const source = href.startsWith('#c') ? byCid.get(href.slice(1)) : undefined
        if (!source) return
        onCite(source)
        return false
      }}
    >
      {md}
    </Markdown>
  )
}
