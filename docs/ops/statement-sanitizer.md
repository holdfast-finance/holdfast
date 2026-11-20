# Statement sanitizer (owner guide)

Turns your real bank, card and brokerage statements into safe fixtures for bank profiles. It runs
offline on your machine. **No agent reads the input.** Agents only ever see the reviewed output.

## Run it

Put the zip (or PDFs, CSVs, OFX/QFX) under `data/inbox/` (git-ignored). Then:

```
make sanitize IN=data/inbox/statements.zip OUT=data/outbox/run1
```

`IN` can be a zip, a directory, or one file. `OUT` is created `0700`; files are `0600`.
Outputs per document: `doc-0001.txt` (layout text), `doc-0001.layout.json` (tokens with
positions), `doc-0001.report.json`, plus a top-level `SUMMARY.json`. Output names are numbered;
original file names are never copied. The report holds counts by kind and a 12-char SHA-256 of
the sanitized text, never a hash or any other value from the source file.

Equivalent: `cd backend && uv run python -m app.importers.sanitize <in> <out>`.

Keep an institution name so profile detection still has its fingerprint:
`make sanitize IN=... OUT=... KEEP="First Example Bank"` (repeat or space-separate words; `KEEP`
words are left as-is, so only keep names that are not yours).

## What it does

- Default-deny: every word that is not a known label (Beginning Balance, Deposits, ...), a date,
  an amount or punctuation is replaced with a synthetic word of the same length and case.
- Names, addresses, merchants, account/card/routing numbers and any other digit run are replaced.
  Emails become `userNNN@example.test`.
- Dates keep their format and their order. Each gap is redrawn, and a clock time is replaced, so
  one known date does not translate the rest of the document.
- Amounts keep their format and sign markers (`-`, parentheses, `CR`/`DR`). Each transaction is a
  fresh `Decimal` on the cent grid, not a multiple of the original. Opening, running balances,
  section totals and closing are recomputed so opening + sum of transactions = closing. On a
  brokerage row, two of quantity, price and value are fresh and the third is their product.
  The report says `reconciled_before`, `reconciled_after` and `reconciliation_survived`.
- Nothing original is logged or printed.

## Review before anyone else sees it

```
make sanitize IN=data/inbox/statements.zip REVIEW=1 | less
```

`REVIEW=1` prints only sanitized text and writes nothing. Dollars in that text are synthetic.
Read it for a leftover name: a nickname, an employer, a rare merchant. If something slipped
through, delete that output and tell Keel the word so it is removed from the label list. Do not
paste originals into chat.

## Where approved output goes

Only after review, copy the files you approve into
`backend/tests/fixtures/statements/<bank>/` (for example `first-example-bank/`). Keep the
`doc-NNNN.txt` and `.layout.json`; leave the `.report.json` out unless you want it. Then hand the
bank name to an agent and say the fixtures are ready. Never copy anything from `data/inbox/`.

## Safety limits

Zip input rejects path traversal (`..`, absolute, backslash, drive letters), symlinks, special
files and encrypted entries. Defaults: 500 files, 50 MiB per file, 500 MiB total counted from
bytes the reader actually returns, a 200x guard on bytes written versus the compressed size, and
50 PDF pages. Python's zip reader also stops at the declared uncompressed size; a header that
does not match the payload fails an integrity check and the run aborts. Extraction goes to a
`0700` temp dir that is removed afterwards, including a member rejected for size or ratio.
Other file types are skipped.

## Known limits

- Scanned or image-only PDFs have no text layer. They are flagged `no_text_layer_needs_ocr` in
  the report and emit nothing useful. OCR is not included.
- Amount ratios do not survive. A paycheck does not stay a fixed multiple of a coffee.
- Percentages such as an APR are replaced along with other amounts.
- `quantity x price = value` is rebuilt from synthetic quantity and price.
- Layout text uses positions from the PDF. Columns line up roughly, not pixel-exact.
- Reconciliation checking is heuristic (it looks for Beginning/Opening/Previous and
  Ending/Closing/New Balance lines and date-led rows). It reports `null` when it cannot tell.
- The word list is intentionally small. Unknown label words get replaced; add them with `--keep`.
