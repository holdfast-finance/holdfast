import { Link } from 'react-router'

import { Button } from '@/components/ui/button'

export function NotFoundPage() {
  return (
    <section aria-labelledby="page-title" className="mx-auto flex max-w-xl flex-col gap-4 py-12">
      <p className="money text-sm text-text-muted">404</p>
      <h1 id="page-title" className="text-3xl">
        Nothing anchored here.
      </h1>
      <p className="text-sm text-text-muted">That page does not exist in this build.</p>
      <Button asChild className="self-start">
        <Link to="/">Back to overview</Link>
      </Button>
    </section>
  )
}
