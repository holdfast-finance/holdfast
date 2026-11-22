import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError, apiUrl, CSRF_HEADER, readData } from '@/api/client'
import type { components } from '@/api/schema'
import { CLIENT_ERROR_CODES } from '@/api/contracts'
import { withReauth } from '@/stores/reauth'

/*
 * Statement import (story I5): upload a PDF, page through import runs, accept a flagged run.
 * Types come from the generated schema. Amounts are decimal strings, always.
 */

type Schemas = components['schemas']

export type ImportRun = Schemas['ImportRunOut']
export type ImportBlock = Schemas['ImportBlockOut']
export type ImportResult = Schemas['ImportResultOut']
export type ImportBalance = Schemas['ImportBalanceOut']
export type ImportAccount = Schemas['ImportAccountOut']
export type ReconcileStatus = ImportRun['reconcile_status']

/** The balances behind a reconcile result: closing - (opening + txn_sum) must be within $0.01. */
export interface ReconcileNumbers {
  opening_balance: string | null
  closing_balance: string | null
  txn_sum: string | null
  balance_diff: string | null
}

/** The importer refuses larger PDFs (20 MiB); the route takes a 21 MiB body (ADR 0020). */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024
export const MAX_UPLOAD_LABEL = '20 MiB'
/** CSV is text: the importer caps it at 10 MiB, and OFX/QFX (tag-heavy) at 5 MiB. */
export const MAX_TEXT_UPLOAD_BYTES = 10 * 1024 * 1024
export const MAX_TEXT_UPLOAD_LABEL = '10 MiB'
export const MAX_OFX_UPLOAD_BYTES = 5 * 1024 * 1024
export const MAX_OFX_UPLOAD_LABEL = '5 MiB'

export const RUNS_PAGE_SIZE = 20

export const importKeys = {
  runs: ['imports', 'runs'] as const,
  accounts: ['imports', 'accounts'] as const,
}

/** Where a CSV or OFX/QFX goes: an existing import account, or a new one. */
export interface UploadTarget {
  account: string // 'new' or an account id
  accountKind: 'bank' | 'card' // for a new CSV account; OFX states its own
}

export const NEW_ACCOUNT = 'new'

/** Import accounts this person can add to. Only fetched once a CSV or OFX is chosen. */
export function useImportAccounts(enabled: boolean) {
  return useQuery({
    queryKey: importKeys.accounts,
    enabled,
    queryFn: ({ signal }) => readData(api.GET('/api/imports/accounts', { signal })),
  })
}

/** Keyset pages, newest first. The cursor is opaque, so pages are only fetched in order. */
export function useImportRuns() {
  return useInfiniteQuery({
    queryKey: importKeys.runs,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      readData(
        api.GET('/api/imports/runs', {
          signal,
          params: { query: { limit: RUNS_PAGE_SIZE, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor,
  })
}

/** An upload the server refused. `retryAfter` is the `Retry-After` header in seconds, if any. */
export class UploadError extends ApiError {
  readonly retryAfter: number | null

  constructor(status: number, code: string, message: string, retryAfter: number | null) {
    super(status, code, message)
    this.name = 'UploadError'
    this.retryAfter = retryAfter
  }
}

function uploadErrorFrom(status: number, body: unknown, retryHeader: string | null): UploadError {
  const seconds = retryHeader === null ? Number.NaN : Number.parseInt(retryHeader, 10)
  const retryAfter = Number.isFinite(seconds) && seconds > 0 ? seconds : null
  if (typeof body === 'object' && body !== null && 'error' in body) {
    const { error } = body as { error?: { code?: unknown; message?: unknown } }
    if (typeof error?.code === 'string' && typeof error.message === 'string') {
      return new UploadError(status, error.code, error.message, retryAfter)
    }
  }
  if (status === 413) {
    return new UploadError(
      status,
      'payload_too_large',
      `That file is over the upload limit (${MAX_UPLOAD_LABEL} for PDF, ${MAX_TEXT_UPLOAD_LABEL} for CSV, ${MAX_OFX_UPLOAD_LABEL} for OFX and QFX).`,
      null,
    )
  }
  return new UploadError(
    status,
    status >= 500 ? CLIENT_ERROR_CODES.serverError : CLIENT_ERROR_CODES.httpError,
    `The server answered HTTP ${String(status)}.`,
    retryAfter,
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
  target?: UploadTarget,
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
        reject(uploadErrorFrom(xhr.status, body, xhr.getResponseHeader('Retry-After')))
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
    if (target && !/\.pdf$/i.test(file.name)) {
      form.append('account', target.account)
      if (target.account === NEW_ACCOUNT) form.append('account_kind', target.accountKind)
    }
    form.append('file', file, file.name)
    xhr.send(form)
  })
}

export function useUploadStatement(onProgress: (fraction: number) => void) {
  const queryClient = useQueryClient()
  return useMutation({
    // The user decides whether to try again; a silent retry would re-send the whole PDF.
    retry: false,
    mutationFn: ({ file, target }: { file: File; target?: UploadTarget }) => {
      onProgress(0)
      return uploadStatement(file, onProgress, undefined, target)
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: importKeys.runs }),
        queryClient.invalidateQueries({ queryKey: importKeys.accounts }),
        queryClient.invalidateQueries({ queryKey: ['ledger'] }),
      ])
    },
  })
}

/** needs_review -> accepted. Sensitive: a stale session gets the step-up prompt, then one retry. */
export function useAcceptRun(onAccepted: (runId: string) => void) {
  const queryClient = useQueryClient()
  return useMutation({
    retry: false,
    mutationFn: (runId: string) =>
      withReauth(() =>
        readData(
          api.POST('/api/imports/runs/{run_id}/accept', { params: { path: { run_id: runId } } }),
        ),
      ),
    onSuccess: async (_data, runId) => {
      onAccepted(runId)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: importKeys.runs }),
        queryClient.invalidateQueries({ queryKey: ['ledger'] }),
      ])
    },
  })
}
