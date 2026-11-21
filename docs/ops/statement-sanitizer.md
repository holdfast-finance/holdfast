# Statement sanitizer (owner guide)

Turns your real bank, card and brokerage statements into safe fixtures for bank profiles. It runs
offline on your machine. Its output is the **only** form of a real statement that may be committed as a
fixture. Whether agents may read the originals on your machine is your call as the instance owner
([ADR 0019](../adr/0019-owner-real-data-stays-local.md)); either way the originals stay outside the repository.

## Run it

Put the zip (or PDFs, CSVs, OFX/QFX) in a private directory **outside the repository**, mode `0700`
(the examples use `~/holdfast-private/`). Never under the repo tree, not even a git-ignored folder. Then:

```
make sanitize IN=~/holdfast-private/inbox/statements.zip OUT=~/holdfast-private/outbox/run1
```

`IN` can be a zip, a directory, or one file. `OUT` is created `0700`; files are `0600`, created
with `O_EXCL|O_NOFOLLOW`, so a planted symlink is never written through. A symlinked `OUT` is
refused. A non-empty `OUT` is refused too, so stale output from an older run cannot mix with a new
one. Add `CLEAR=1` (CLI: `--clear-out`) to empty an existing `OUT` first. It only ever deletes this
tool's own output names (`doc-NNNN.txt`, `.layout.json`, `.report.json`, `SUMMARY.json`). Every
entry is checked before the first delete, and anything else (a PDF, a subdirectory, an unrelated
file) makes it refuse and delete nothing. `OUT` may not equal `IN` or contain it or sit inside
it, so `OUT=~/holdfast-private/inbox CLEAR=1` is refused.
Outputs per document: `doc-0001.txt` (layout text), `doc-0001.layout.json` (tokens with
positions), `doc-0001.report.json`, plus a top-level `SUMMARY.json`. Output names are numbered;
original file names are never copied. The report holds counts by kind and a 12-char SHA-256 of
the sanitized text, never a hash or any other value from the source file.

Equivalent: `cd backend && uv run python -m app.importers.sanitize <in> <out>`.

Keep an institution name so profile detection still has its fingerprint:
`make sanitize IN=... OUT=... KEEP="First Example Bank"` (repeat or space-separate words; `KEEP`
words are left as-is, so only keep names that are not yours). Words that double as common names
or merchants (`mobile`, `card`, `stock`) are not in the label list. If a statement needs them as
labels, pass them back with `--keep mobile --keep card --keep stock`.

## What it does

- Default-deny: every word that is not a known label (Beginning Balance, Deposits, ...), a date,
  an amount or ASCII punctuation is replaced with a synthetic word of the same length and case.
  Letters in any script (Cyrillic, Greek, CJK, fullwidth, accented) count as words, including next
  to punctuation. Non-ASCII symbols, combining marks and exotic spaces are replaced one for one;
  only ASCII punctuation and whitespace pass through.
- Names, addresses, merchants, account/card/routing numbers and any other digit run are replaced.
  Emails become `userNNN@example.test`.
- Dates keep their format and their order. Each gap is redrawn, and a clock time is replaced, so
  one known date does not translate the rest of the document.
- Amounts keep their format and sign markers (`-`, parentheses, `CR`/`DR`). Each transaction is a
  fresh `Decimal` on the cent grid, not a multiple of the original. Opening, running balances,
  section totals and closing are recomputed so opening + sum of transactions = closing. On a
  brokerage row, two of quantity, price and value are fresh and the third is their product.
  The report says `reconciled_before`, `reconciled_after` and `reconciliation_survived`.
- `layout.json` carries no source glyph widths. PDF tokens snap to the character-cell grid, and
  `x1 - x0` is the sanitized text length times the cell width. Amounts anchor on their snapped
  right edge, other tokens on their snapped left edge. A reader cannot rank surnames by width or
  count the digits of an amount from the geometry.
- Thousands grouping (`1,234.56` versus `1234.56`) is one choice per document, applied to the
  synthetic value. It is never copied from the original token, so it does not reveal a magnitude.
- OFX/QFX: every piece of text is sanitized, including bare lines, text before the first tag,
  `<![CDATA[...]]>` bodies, `<!-- -->` comments and text after a closing tag. Header lines and
  `<?xml?>` / `<?OFX?>` prolog attributes pass only with a spec value for that key (for example
  `OFXHEADER` 100 or 200, `VERSION` 102 to 221, `SECURITY` NONE or TYPE1). Anything else is
  sanitized. Enum values (`TRNTYPE`, `ACCTTYPE`, `CURDEF`, ...) stay only when they match the spec
  list or ISO 4217. Element names outside the OFX 1.x/2.x list (and the `INTU.` prefix) are
  renamed `X.TAG<n>`, consistently within a document, and counted as `renamed_tags` in the report.
- Nothing original is logged or printed. An unexpected error prints its type only, never a message
  or a path.

## Review before anyone else sees it

```
make sanitize IN=~/holdfast-private/inbox/statements.zip REVIEW=1 | less
```

`REVIEW=1` prints only sanitized text and writes nothing. Dollars in that text are synthetic.
Read it for a leftover name: a nickname, an employer, a rare merchant. If something slipped
through, delete that output and tell Keel the word so it is removed from the label list. Do not
paste originals into chat.

## Where approved output goes

Only after review, copy the files you approve into
`backend/tests/fixtures/statements/<bank>/` (for example `first-example-bank/`). Keep the
`doc-NNNN.txt` and `.layout.json`; leave the `.report.json` out unless you want it. Then hand the
bank name to an agent and say the fixtures are ready. Never copy anything from the private inbox into the repository.

## Safety limits

The file and byte budget is one per run, shared by every zip and plain file together. Zip
members count against it before anything is extracted. Zip input rejects path traversal (`..`, absolute, backslash, drive letters), symlinks, special
files and encrypted entries. Defaults: 500 files, 50 MiB per file, 500 MiB total counted from
bytes the reader actually returns, a 200x guard on bytes written versus the compressed size, and
50 PDF pages. Python's zip reader also stops at the declared uncompressed size; a header that
does not match the payload fails an integrity check and the run aborts. A reader that returns
more bytes than the header declared is rejected too. Extraction goes to a
`0700` temp dir that is removed afterwards, including a member rejected for size or ratio.
Other file types are skipped. An unsupported compression method, a truncated stream or an I/O
error ends the run with a fixed message that never includes a path.

## Known limits

- PDF parsing (pdfplumber) runs in-process with no memory or time limit beyond the 50-page cap.
  A hostile PDF could exhaust RAM or CPU. A portable limit (RLIMIT_AS is Linux and macOS only, and
  a timeout needs a subprocess) is not built; only run the tool on files you downloaded yourself.

- Scanned or image-only PDFs have no text layer. They are flagged `no_text_layer_needs_ocr` in
  the report and emit nothing useful. OCR is not included.
- Amount ratios do not survive. A paycheck does not stay a fixed multiple of a coffee.
- Percentages such as an APR are replaced along with other amounts.
- `quantity x price = value` is rebuilt from synthetic quantity and price.
- Layout text uses positions from the PDF. Columns line up roughly, not pixel-exact.
- Reconciliation checking is heuristic (it looks for Beginning/Opening/Previous and
  Ending/Closing/New Balance lines and date-led rows). It reports `null` when it cannot tell.
- The word list is intentionally small. Unknown label words get replaced; add them with `--keep`.
