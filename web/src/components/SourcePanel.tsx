import { Drawer, DrawerHeader } from '@astryxdesign/core/Drawer'
import { Layout, LayoutContent, LayoutFooter } from '@astryxdesign/core/Layout'
import { Link } from '@astryxdesign/core/Link'
import { Text } from '@astryxdesign/core/Text'
import { VStack } from '@astryxdesign/core/VStack'
import type { Source } from '@/api'

const EURLEX = 'https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679'

/** The cited passage, in a non-modal drawer so other citations stay clickable while it's open. */
export function SourcePanel({
  source,
  isOpen,
  onOpenChange,
}: {
  source: Source
  isOpen: boolean
  onOpenChange: (isOpen: boolean) => void
}) {
  return (
    <Drawer isOpen={isOpen} onOpenChange={onOpenChange} label="Source passage" width={420} hasScrim={false}>
      <Layout
        header={<DrawerHeader title={source.label} subtitle={source.heading} onOpenChange={onOpenChange} hasDivider />}
        content={
          <LayoutContent>
            <VStack gap={3}>
              {source.text
                .split('\n')
                .filter((line) => line.trim())
                .map((line, i) => (
                  <Text key={i} as="p">
                    {line}
                  </Text>
                ))}
            </VStack>
          </LayoutContent>
        }
        footer={
          source.source === 'gdpr' && (
            <LayoutFooter hasDivider>
              <Text type="supporting">
                Regulation (EU) 2016/679 ·{' '}
                <Link href={`${EURLEX}#${source.ref}`} isExternalLink>
                  Open on EUR-Lex
                </Link>
              </Text>
            </LayoutFooter>
          )
        }
      />
    </Drawer>
  )
}
