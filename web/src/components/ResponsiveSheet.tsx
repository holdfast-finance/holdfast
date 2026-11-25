import type { ComponentProps, ReactNode } from 'react'

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { useIsDesktop } from '@/hooks/useBreakpoint'

/** Bottom sheet on phones, side sheet on desktop. Used instead of modals (frontend rules). */
export function ResponsiveSheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  onCloseAutoFocus,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  onCloseAutoFocus?: ComponentProps<typeof SheetContent>['onCloseAutoFocus']
}) {
  const isDesktop = useIsDesktop()
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side={isDesktop ? 'right' : 'bottom'}
        className={cn(
          'gap-5 overflow-y-auto p-5 pb-[max(2rem,env(safe-area-inset-bottom))]',
          !isDesktop && 'max-h-[90dvh]',
        )}
        onCloseAutoFocus={onCloseAutoFocus}
        onOpenAutoFocus={(event) => {
          // Don't focus the first field: on iOS that pops the keyboard over the sheet.
          event.preventDefault()
          if (event.target instanceof HTMLElement) event.target.focus()
        }}
        {...(description ? {} : { 'aria-describedby': undefined })}
      >
        <SheetHeader className="p-0">
          <SheetTitle>{title}</SheetTitle>
          {description && <SheetDescription>{description}</SheetDescription>}
        </SheetHeader>
        {children}
      </SheetContent>
    </Sheet>
  )
}
