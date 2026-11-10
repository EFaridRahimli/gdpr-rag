import type { ReactNode } from 'react'
import { Heading } from '@astryxdesign/core/Heading'
import { Layout, LayoutContent } from '@astryxdesign/core/Layout'
import { Text } from '@astryxdesign/core/Text'
import { VStack } from '@astryxdesign/core/VStack'

/** A scrolling page with a narrow centred reading column: the frame every non-chat view shares. */
export function Page({
  title,
  description,
  width = 680,
  children,
}: {
  title: string
  description?: ReactNode
  width?: number
  children: ReactNode
}) {
  return (
    <Layout
      contentWidth={width}
      content={
        <LayoutContent padding={6}>
          <VStack gap={8} paddingBlock={4}>
            <VStack gap={2}>
              <Heading level={1}>{title}</Heading>
              {description && (
                <Text as="p" color="secondary">
                  {description}
                </Text>
              )}
            </VStack>
            {children}
          </VStack>
        </LayoutContent>
      }
    />
  )
}
