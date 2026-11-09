import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import type { Source } from '@/api'
import { Button } from '@/components/ui/button'

const EURLEX = 'https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679'

export function SourcePanel({ source, onClose }: { source: Source; onClose: () => void }) {
  const title = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    title.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [source, onClose])

  return (
    <aside
      aria-label="Source passage"
      className="fixed inset-0 z-30 flex flex-col bg-background md:static md:w-[420px] md:shrink-0 md:border-l"
    >
      <div className="flex items-start justify-between gap-4 border-b px-6 py-5">
        <div>
          <h2 ref={title} tabIndex={-1} className="font-medium outline-none">
            {source.label}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{source.heading}</p>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close source">
          <X />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-5 text-[15px] leading-7 whitespace-pre-wrap">
        {source.text}
      </div>
      {source.source === 'gdpr' && (
        <div className="border-t px-6 py-4 text-sm text-muted-foreground">
          Regulation (EU) 2016/679 ·{' '}
          <a href={`${EURLEX}#${source.ref}`} target="_blank" rel="noreferrer" className="underline underline-offset-4">
            Open on EUR-Lex
          </a>
        </div>
      )}
    </aside>
  )
}
