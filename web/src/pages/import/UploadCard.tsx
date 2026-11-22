import { zodResolver } from '@hookform/resolvers/zod'
import { FileUp, LoaderCircle } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { z } from 'zod'

import {
  MAX_OFX_UPLOAD_BYTES,
  MAX_OFX_UPLOAD_LABEL,
  MAX_TEXT_UPLOAD_BYTES,
  MAX_TEXT_UPLOAD_LABEL,
  MAX_UPLOAD_BYTES,
  MAX_UPLOAD_LABEL,
  NEW_ACCOUNT,
  UploadError,
  useImportAccounts,
  useUploadStatement,
  type ImportResult,
  type UploadTarget,
} from '@/api/imports'
import { ApiError } from '@/api/client'
import { FormError } from '@/components/forms/Field'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ReauthCancelledError } from '@/stores/reauth'
import { resultHeadline } from './format'
import { AccountPicker } from './AccountPicker'
import { ResultView } from './ResultView'

const isCsv = (f: File): boolean => /\.csv$/i.test(f.name) || f.type === 'text/csv'
const isOfx = (f: File): boolean => /\.(ofx|qfx)$/i.test(f.name)
const isText = (f: File): boolean => isCsv(f) || isOfx(f)

const schema = z.object({
  file: z
    .custom<File>((v) => v instanceof File, 'Choose a statement file.')
    .refine(
      (f) => isText(f) || /\.pdf$/i.test(f.name) || f.type === 'application/pdf',
      'That is not a PDF, CSV or OFX/QFX file. Choose the export from your bank.',
    )
    .refine(
      (f) => isText(f) || f.size <= MAX_UPLOAD_BYTES,
      `That file is over the ${MAX_UPLOAD_LABEL} upload limit. Export a smaller file and try again.`,
    )
    .refine(
      (f) => !isCsv(f) || f.size <= MAX_TEXT_UPLOAD_BYTES,
      `That file is over the ${MAX_TEXT_UPLOAD_LABEL} upload limit. Export a smaller file and try again.`,
    )
    .refine(
      (f) => !isOfx(f) || f.size <= MAX_OFX_UPLOAD_BYTES,
      `That file is over the ${MAX_OFX_UPLOAD_LABEL} upload limit. Export a smaller file and try again.`,
    ),
})
type FormValues = z.infer<typeof schema>

function size(bytes: number): string {
  if (bytes < 1024) return `${String(bytes)} B`
  const mib = 1024 * 1024
  if (bytes < mib) return `${(bytes / 1024).toFixed(0)} KiB`
  return `${(bytes / mib).toFixed(1)} MiB`
}

/** The server's fixed messages are already plain; add when to retry for a 429. */
function uploadError(error: unknown): unknown {
  if (error instanceof ReauthCancelledError)
    return new ApiError(0, 'cancelled', 'Import cancelled.')
  if (error instanceof UploadError && error.status === 429 && error.retryAfter !== null) {
    const when = `${String(error.retryAfter)} second${error.retryAfter === 1 ? '' : 's'}`
    return new ApiError(error.status, error.code, `${error.message} Try again in ${when}.`)
  }
  return error
}

export function UploadCard({
  result,
  setResult,
  onAccepted,
}: {
  result: ImportResult | null
  setResult: (result: ImportResult | null) => void
  onAccepted: (runId: string) => void
}) {
  const inputId = useId()
  const descId = useId()
  const errorId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [progress, setProgress] = useState(0)
  const upload = useUploadStatement(setProgress)
  const form = useForm<FormValues>({ resolver: zodResolver(schema) })
  const file = useWatch({ control: form.control, name: 'file' }) as File | undefined
  const fileError = form.formState.errors.file?.message
  const [target, setTarget] = useState<UploadTarget>({ account: NEW_ACCOUNT, accountKind: 'bank' })
  const textFile = file !== undefined && isText(file)
  const accounts = useImportAccounts(textFile)

  const submit = form.handleSubmit(({ file: chosen }) => {
    setResult(null)
    setProgress(0)
    upload.mutate(
      { file: chosen, target: isText(chosen) ? target : undefined },
      {
        onSuccess: (data) => {
          setResult(data)
        },
      },
    )
  })

  const reset = () => {
    setResult(null)
    upload.reset()
    form.reset({ file: undefined })
    setTarget({ account: NEW_ACCOUNT, accountKind: 'bank' })
    if (inputRef.current) inputRef.current.value = ''
  }

  const busy = upload.isPending
  const progressText = progress < 1 ? 'Uploading…' : 'Reading the statement…'
  const headline = result ? resultHeadline(result) : ''
  // Accept closes the sheet and focuses a heading. Announce only the upload outcome here.
  const uploadHeadline = upload.data ? resultHeadline(upload.data) : ''

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">Import a statement</CardTitle>
        <CardDescription id={descId}>
          PDF up to {MAX_UPLOAD_LABEL}. CSV up to {MAX_TEXT_UPLOAD_LABEL}, OFX/QFX up to{' '}
          {MAX_OFX_UPLOAD_LABEL}. Stored with your household's documents. Upload the same file twice
          and nothing is added.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div role="status" aria-atomic="true" className="sr-only">
          {busy
            ? progressText
            : headline === uploadHeadline
              ? [headline, upload.data?.notice].filter(Boolean).join('. ')
              : ''}
        </div>
        {result ? (
          <>
            <ResultView result={result} headline={headline} onAccepted={onAccepted} />
            <Button size="lg" variant="outline" onClick={reset}>
              Import another statement
            </Button>
          </>
        ) : (
          <form
            onSubmit={(e) => void submit(e)}
            noValidate
            aria-label="Import a statement"
            className="flex flex-col gap-4"
          >
            <div className="flex flex-col gap-2">
              <label
                htmlFor={inputId}
                className="relative flex min-h-14 cursor-pointer items-center gap-3 rounded-lg border border-dashed border-border bg-muted/40 px-4 py-3 text-sm font-medium focus-within:ring-3 focus-within:ring-ring"
              >
                <FileUp aria-hidden="true" className="size-5 shrink-0 text-primary-text" />
                <span className="min-w-0 flex-1 truncate">
                  {file ? file.name : 'Choose a statement file'}
                </span>
                {file && (
                  <span className="shrink-0 text-xs text-muted-foreground">{size(file.size)}</span>
                )}
                <input
                  ref={inputRef}
                  id={inputId}
                  type="file"
                  accept="application/pdf,.pdf,.csv,text/csv,.ofx,.qfx"
                  className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                  disabled={busy}
                  aria-label={file ? `Statement file: ${file.name}` : 'Choose a statement file'}
                  aria-describedby={fileError ? `${descId} ${errorId}` : descId}
                  aria-invalid={fileError ? true : undefined}
                  onChange={(e) => {
                    upload.reset()
                    const chosen = e.target.files?.[0]
                    form.setValue('file', chosen as File, { shouldValidate: true })
                  }}
                />
              </label>
              {fileError && (
                <p id={errorId} role="alert" className="text-sm text-destructive-text">
                  {fileError}
                </p>
              )}
            </div>

            {textFile && (
              <AccountPicker
                accounts={accounts.data ?? []}
                loading={accounts.isPending}
                isCsv={isCsv(file)}
                value={target}
                onChange={setTarget}
                disabled={busy}
              />
            )}

            {busy && (
              <div className="flex flex-col gap-1.5">
                <div
                  role="progressbar"
                  aria-label="Upload progress"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(progress * 100)}
                  className="h-2 overflow-hidden rounded-full bg-muted"
                >
                  <div
                    className="h-full bg-primary transition-[width]"
                    style={{ width: `${String(Math.round(progress * 100))}%` }}
                  />
                </div>
                <p aria-hidden="true" className="text-xs text-muted-foreground">
                  {progressText}
                </p>
              </div>
            )}

            {upload.isError && <FormError error={uploadError(upload.error)} />}

            <Button type="submit" size="lg" disabled={busy || !file}>
              {busy && <LoaderCircle aria-hidden="true" className="animate-spin" />}
              {busy ? 'Importing…' : upload.isError ? 'Try again' : 'Import statement'}
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  )
}
