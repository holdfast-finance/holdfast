import { PAGES, type PageId } from '@/app/nav'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

export function PlaceholderPage({ id }: { id: PageId }) {
  const page = PAGES[id]
  return (
    <section aria-labelledby="page-title" className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 id="page-title" className="text-3xl md:text-4xl">
          {page.label}
        </h1>
        <p className="text-sm text-muted-foreground">{page.summary}</p>
        <p className="text-xs text-secondary-text">Coming in Phase {page.phase}</p>
      </header>
      <Card className="rounded-card border border-border ring-0">
        <CardContent className="flex flex-col gap-3" aria-hidden="true">
          <Skeleton className="h-6 w-1/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    </section>
  )
}
