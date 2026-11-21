import { RunsSections } from './RunsLists'
import { UploadCard } from './UploadCard'

export function ImportPage() {
  return (
    <section aria-labelledby="page-title" className="mx-auto flex max-w-3xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 id="page-title" className="text-3xl md:text-4xl">
          Import
        </h1>
        <p className="text-sm text-muted-foreground">
          Bring a statement in. Holdfast checks that opening plus transactions equals closing.
        </p>
      </header>
      <UploadCard />
      <RunsSections />
    </section>
  )
}
