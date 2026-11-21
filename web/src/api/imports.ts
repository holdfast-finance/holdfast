import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, CSRF_HEADER, apiUrl } from '@/api/client'
import { CLIENT_ERROR_CODES } from '@/api/contracts'
import { withReauth } from '@/stores/reauth'

/*
 * Statement import (story I5).
 *
 * CONTRACT STATUS: the backend has the import service (app/services/statement_import.py) but no
 * HTTP routes yet, so `schema.d.ts` has no import paths. The types and the two routes below are
 * the contract this screen needs, written by hand. When the routes land, replace the types with
 * `components['schemas'][...]` and keep the names. Amounts are decimal strings, always.
 *
 *   POST /api/imports/statements   multipart/form-data, one `file` part (PDF)
 *        200 ImportResult  (also for a file that was already imported: `created` is false)
 *        413 payload_too_large, 422 validation_error (not a PDF / unreadable), 403 reauth_required
 *   GET  /api/imports/runs         -> { items: ImportRun[] } newest first
 */

export type ReconcileStatus = 'pending' | 'reconciled' | 'needs_review' | 'accepted'
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed'

/** The balances behind a reconcile result: closing - (opening + txn_sum) must be within $0.01. */
export interface ReconcileNumbers {
  opening_balance: string | null
  closing_balance: string | null
  txn_sum: string | null
  balance_diff: string | null
}

export interface ImportRun extends ReconcileNumbers {
  id: string
  document_id: string
  filename: string
  account_id: string | null
  account_name: string | null
  currency: string
  status: RunStatus
  reconcile_status: ReconcileStatus
  period_start: string | null
  period_end: string | null
  rows_parsed: number
  rows_inserted: number
  rows_duplicate: number
  /** Exception class name only; the server never sends parser messages (they can hold digits). */
  error_class: string | null
  created_at: string
}

/** One account's block of the uploaded statement. */
export interface ImportBlock extends ReconcileNumbers {
  run_id: string
  account_id: string
  account_name: string
  currency: string
  reconcile_status: ReconcileStatus
  period_start: string | null
  period_end: string | null
  rows_parsed: number
  rows_inserted: number
  rows_duplicate: number
  /** False: this file was already imported and nothing was written. */
  created: boolean
}

export interface ImportResult {
  document_id: string
  filename: string
  balances_only: boolean
  blocks: ImportBlock[]
}

/** Caddy caps `/api/*` request bodies at 1 MiB and the API repeats it (ADR 0010). */
export const MAX_UPLOAD_BYTES = 1024 * 1024
export const MAX_UPLOAD_LABEL = '1 MiB'

export const importKeys = {
  runs: ['imports', 'runs'] as const,
}

export function useImportRuns() {
  return useQuery({
    queryKey: importKeys.runs,
    // Not in the generated paths yet, so plain same-origin fetch with the same error mapping.
    queryFn: ({ signal }) => getRuns(signal),
    select: (data) => data.items,
  })
}

async function getRuns(signal: AbortSignal): Promise<{ items: ImportRun[] }> {
  const response = await fetch(apiUrl('/api/imports/runs'), {
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
    signal,
  })
  if (!response.ok) throw errorFrom(response.status, await safeJson(response))
  return (await response.json()) as { items: ImportRun[] }
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFrom(status: number, body: unknown): ApiError {
  if (typeof body === 'object' && body !== null && 'error' in body) {
    const { error } = body as { error?: { code?: unknown; message?: unknown } }
    if (typeof error?.code === 'string' && typeof error.message === 'string') {
      return new ApiError(status, error.code, error.message)
    }
  }
  return new ApiError(
    status,
    status >= 500 ? CLIENT_ERROR_CODES.serverError : CLIENT_ERROR_CODES.httpError,
    `The server answered HTTP ${String(status)}.`,
  )
}

/**
 * Multipart upload over XHR: `fetch` can't report upload progress, and on a phone over a tailnet
 * progress is the difference between "working" and "frozen".
 */
export function uploadStatement(
  file: File,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<ImportResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', apiUrl('/api/imports/statements'))
    xhr.withCredentials = true
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.setRequestHeader(CSRF_HEADER, '1')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total)
    }
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        // Not JSON: fall back to a generic error below.
      }
      if (xhr.status >= 200 && xhr.status < 300 && body !== null) {
        onProgress(1)
        resolve(body as ImportResult)
      } else {
        reject(errorFrom(xhr.status, body))
      }
    }
    xhr.onerror = () => {
      reject(
        new ApiError(0, CLIENT_ERROR_CODES.networkError, 'Holdfast could not reach its server.'),
      )
    }
    xhr.onabort = () => {
      reject(new DOMException('Upload cancelled', 'AbortError'))
    }
    signal?.addEventListener('abort', () => {
      xhr.abort()
    })
    const form = new FormData()
    form.append('file', file, file.name)
    xhr.send(form)
  })
}

export function useUploadStatement(onProgress: (fraction: number) => void) {
  const queryClient = useQueryClient()
  return useMutation({
    // The user decides whether to try again; a silent retry would re-send the whole PDF.
    retry: false,
    // Statement upload is a sensitive action: a stale session gets the step-up prompt, then one retry.
    mutationFn: (file: File) =>
      withReauth(() => {
        onProgress(0)
        return uploadStatement(file, onProgress)
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: importKeys.runs }),
        queryClient.invalidateQueries({ queryKey: ['ledger'] }),
      ])
    },
  })
}
