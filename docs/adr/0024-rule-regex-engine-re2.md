# 0024. Rule patterns run on RE2 (google-re2), not Python `re`

- Status: Accepted
- Date: 2026-11-21 (revised twice the same day after Bulwark's reviews of PR #59)
- Deciders: Lattice (architect); Keel. Bulwark verdict required on the implementing PR (C3d).

## Context
PLAN 5 lists `desc_regex` among rule conditions. C3a (`app/domain/rules.py`, PR #53) accepts a
regex mode behind a static validator:
- patterns of up to 128 characters
- at most one unbounded repeat
- limited alternation
- descriptions capped at 1,024 characters

Bulwark showed the validator isn't enough. `A?A?A?A?A*` followed by 117 `A`s and a `B` passes it,
and takes about 0.95 s for one search of a 1,024-character description. We reproduced this on
2026-11-21 (0.951 s, CPython 3.12).

Rules run over every live row in a retroactive preview (tens of thousands of rows in the request
path) and on every ingested row. One saved pattern like that is a denial of service against the
API and the worker. PR #53's gate: no user-written pattern may be saved or run until a linear-time
engine or a killable subprocess is chosen.

The preview and apply contract (PHASE-4 contract, C3e) also needs determinism. The count a person
approves must be the count that's written. Anything that can match or not depending on load breaks
that.

**RE2 is linear, but its constant isn't small** (Bulwark, re-measured by Lattice).

Bulwark's case:
- RE2 searches with a lazily built DFA under a memory budget (`max_mem`). When a pattern's DFA
  needs more states than the budget allows, RE2 keeps flushing the cache and finally falls back to
  the NFA.
- That's still linear in the subject, but hundreds of times slower per character.
- An unanchored prefix followed by a counted class (`[AB]*A[ABC]{20}D`) is enough to cause it.

Lattice's measurements, 2026-11-21:
- Setup:
  - CPython 3.12.16, `google-re2==1.1.20251105`, an x86_64 laptop
  - options as in point 3
  - random subjects over `{A,B,C}`, unless noted
- At 1,024 characters and `max_mem = 1 MiB`:
  - `[AB]*A[ABC]{20}D`: 258 µs per search
  - `A[ABC]{120}D`: 537 µs per search
  - `.*A.{30}B.{30}D`: 568 µs per search
  - 50,000 searches of each took 12.3 s, 27.1 s and 27.6 s. These numbers match Bulwark's.
- On description-like subjects (upper case, digits, spaces, `*#-./`), the same three run at
  1.3–3.2 µs.
- PR #53's slow pattern and `(x+x+)+y` run at about 2.0 µs: 0.1 s for 50,000 searches.
- A larger `max_mem` doesn't help at 1,024 characters: at 8 MiB the three run at 295, 576 and
  608 µs.
- A shorter subject helps. At 256 characters and 1 MiB, the three run at 63, 123 and 140 µs.
- So the old gate ("1,000 searches under 1 s") passes patterns that make a 50,000-row preview take
  about 28 s.

## Decision
1. **Engine: `google-re2`, pinned `==1.1.20251105`.** It's the official Python binding of Google's
   RE2 (the `python/` directory of `github.com/google/re2`). RE2 compiles to automata and matches in
   time linear in the subject, with no backtracking. Facts checked against PyPI on 2026-11-21:
   - Licence: BSD-3-Clause (OSI "BSD License" classifier), compatible with AGPL-3.0.
   - Wheels for CPython 3.10 to 3.14, including `manylinux_2_28` **x86_64 and aarch64**, macOS and
     Windows (`win_amd64`, `win_arm64`).
   - About 0.6 MB, with no runtime dependencies (abseil is linked in).
   - Our image is `python:3.12-slim-bookworm` (glibc 2.36, above 2.28).
   - No `musllinux` wheel: an Alpine image would have to build from source. We don't use Alpine.
2. **One module owns it, and nothing falls back.**
   - `app/domain/rule_regex.py` is the only importer of `re2`. A test fails on `import re2`
     anywhere else.
   - It exposes `compile_pattern(pattern: str) -> CompiledPattern` (`.search(text) -> bool`) and
     raises `RuleValidationError` with a fixed message.
   - **No fallback to `re` or `regex`**, ever. `rule_regex.py` never imports `re` or `regex`, and
     neither do `app/domain/rules.py` or `app/services/rules.py`. A test parses the three modules
     (`ast`) and fails if they do.
   - If `import re2` fails at runtime, `compile_pattern` raises `RuleValidationError("Patterns
     aren't available on this server.")`.
   - The rule loader then skips every `regex` rule. Such rules aren't run at ingest, in preview or
     in apply. The API reports them as `status = "unavailable"`, computed on read with no column,
     and the skip is logged as a count. The API refuses new ones.
   - Before C3d lands, `compile_pattern` raises `RuleValidationError("Patterns aren't available
     yet.")` for every input. So the codec, the loader and the API all refuse `regex`, whoever the
     writer is.
3. **Options:**
   - `case_sensitive=False`, `never_capture=True`, `log_errors=False`
   - `max_mem = 1 << 20` (1 MiB per compiled pattern)

   `log_errors=False` matters: by default RE2 writes a rejected pattern to stderr through abseil
   logging (observed), and rule text is user content that must not reach logs.
4. **Validation is simple.** The C3a structural validator is deleted (C3c). A pattern is accepted
   when it:
   - is 1 to 128 characters and contains no NUL
   - compiles under the options above
   - doesn't match the empty string. A pattern that matches `""` matches every description, which
     makes a catch-all rule, and Ledger rejected those in C3a.
   - passes the cost probe (4b)

   Error messages never echo the pattern.

   4b. **Save-time cost probe.** `compile_pattern` times the compiled pattern against a probe set,
   and rejects it with a fixed message ("This pattern is too slow to run on every transaction.")
   when a probe family's mean exceeds **10 µs per search** (`PROBE_LIMIT_MICROSECONDS`).
   - **Probe set:** 64 fixed synthetic subjects of `MAX_SUBJECT_LENGTH` characters:
     - 24 over `{A,B,C}`
     - 8 over `{A,B}`
     - 31 description-like
     - one run of `A`

     Bulwark's text, adopted as written: "Plus 32 subjects for every class atom in the pattern:
     bracket classes, including negated ones (complemented within printable ASCII), escapes (`\d \D
     \w \W \s \S \pL \PL \pN`), POSIX classes and `.`. Each atom also gets 32 subjects drawn half from
     its alphabet and half from the pattern's literal characters. A pattern with more than 16
     distinct class atoms is rejected as too complex." (`MAX_CLASS_ATOMS`.)
   - **Two amendments, both measured:**
     - **Per-literal mixed families.** The "half from the pattern's literal characters" subjects
       are drawn per literal: for each literal character (up to 32), 32 subjects, half from the
       atom's alphabet and half that one character. A family that mixes all the literals together
       still accepted `\w*Q\w{20}#` (128 µs per search on subjects over `{Q,A}`) and
       `[[:digit:]]*1[[:digit:]]{20}#` (65 µs over `{0,1}`), because the terminator `#` keeps
       breaking the chain the blow-up needs.
     - **The limit applies to each family's mean, not the whole set's.** With up to 16 atoms, one
       costly family is a small share of the set, and decoy atoms would dilute it below the limit.
       The probe stops at the first family over the limit.
   - **Classes are found by a character scan, not `re`.** Repeat counts (`{n,m}`) aren't literals.
     Generated letters are upper case, because matching is case-insensitive.
   - **Why derived subjects at all:** a fixed set alone accepted `[XY]*X[XYZ]{20}W` at 1.1 µs, while
     the same pattern costs 335 µs per search on subjects over its own alphabet.
   - **Timing:** each pass compiles the pattern afresh, because the DFA cache is per compiled
     object. A warm second pass hid every blow-up in our measurements. Each family keeps the best of
     three cold passes, which cuts false rejections under load.
   - **Measured** (2026-11-21, round 2; worst family mean, cold, best of three):
     - **Bulwark's three evaders are rejected:**
       - `(?:[J][K][V][Q])?[XY]*X[XYZ]{20}W`: 68 µs
       - `\d*[0-4]\d{20}#`: 64 µs
       - `[^5-9]\d{20}#`: 71 µs

       Bulwark measured them passing the revision-2 probe at about 1 µs.
     - Also rejected:
       - a 13-decoy variant of the first: 72 µs
       - a 5-decoy variant of `[AB]*A[ABC]{20}D`: 70 µs
       - `\w*Q\w{20}#`: 110–117 µs
       - `[[:digit:]]*1[[:digit:]]{20}#`: 70–73 µs
       - `[A-Z ]*E[A-Z ]{20}Q`: 106 µs
       - `[A-Z0-9 ]*X[A-Z0-9 ]{14}Q`: 93 µs
       - the three shapes above: 63, 117 and 139 µs
     - Still accepted at 0.9–2.4 µs:
       - literals and alternations (`AMZN MKTP|AMAZON\.COM`, `UBER (EATS|TRIP)`)
       - anchored classes (`^[A-Z]+ [0-9]{4}$`)
       - `\d{3}-\d{4}`, `[^0-9]+ PAYMENT`, `(?:VENMO|ZELLE|PAYPAL) \*?[A-Z ]+`
       - PR #53's two patterns
       - `[^A-W]{1}[XYZ]{120}#`. Every character of its class starts and continues a run, so its
         DFA stays small (2.1 µs on its own alphabet).
     - It costs 0.3–3 ms per accepted pattern, and up to about 15 ms to reject one.
   - **The probe affects acceptance only, never matching.** A stored pattern is never re-probed,
     and its matches never depend on load, so preview count = applied count still holds. A
     borderline pattern may be accepted on one save and refused on another. That's accepted:
     refusal is the safe side.
5. **Subjects:** regex conditions search the **first 256 characters** (`MAX_SUBJECT_LENGTH`) of
   the normalised description (`normalize_description`: upper case, collapsed whitespace).
   - Bank and card descriptions are far shorter than that.
   - Truncating cuts the worst case by four, as measured above.
   - C3a skipped longer descriptions, which made a rule silently miss them. Truncating is bounded and
     predictable.
6. **Syntax differences from `re` are documented for people writing rules** (`docs/user/rules.md`,
   C3d):
   - no backreferences, no lookahead or lookbehind
   - repeat counts of at most 1,000
   - `\d`, `\w`, `\s` and `\b` are ASCII-only (`\b` treats `é` as a non-word character)
   - `\pL` and `[[:alpha:]]` are available
   - matching is case-insensitive
   - a pattern can be refused as too slow (4b)
7. **Gate (C3d, required on CI).** The corpus is:
   - Bulwark's two slow patterns from PR #53
   - **the three DFA blow-up shapes above** (counted classes after an unanchored prefix)
   - a generated adversarial set: nested quantifiers, alternation blow-ups, 128-character patterns,
     and alphabet-shifted variants of the blow-up shapes

   **Each corpus pattern must either be rejected by `compile_pattern` or run 50,000 searches of
   1,024-character subjects in under 1 s on CI.**
   - Bulwark's text, adopted as written: "Gate subjects are independent of the probe: for each
     corpus pattern, 1,024-character subjects over the union of every atom's alphabet, plus the
     probe families. The corpus adds decoy-class (five or more classes), escape-class,
     negated-class and POSIX variants of the three shapes."
   - The corpus also holds Lattice's two per-literal cases (`\w*Q\w{20}#`,
     `[[:digit:]]*1[[:digit:]]{20}#`).
   - The subjects are truncated to 256 by `search`, as in production.
   - The three shapes can only pass by rejection: they take 12–28 s.
   - Measured locally:
     - every pattern the round-2 probe accepted took 0.04–0.10 s for 50,000 searches over the
       union-alphabet subjects plus the probe families
     - the borderline patterns the revision-2 probe rejected would have taken 0.39–0.67 s
   - That leaves better than 6× headroom for free-tier runners. C3d records its CI numbers in the
     PR.
8. **Cap: 25 enabled `regex` rules per household.** The `rule_caps` trigger enforces it, counting
   every person's rules (PHASE-4 contract 3.1). With 4b's class coverage, every accepted pattern
   costs at most the probe limit on its own alphabet. The cap then bounds ingest and preview at
   about 25 × 10 µs per row. The count crosses people, so a 409 at the cap tells one person how
   many enabled pattern rules exist in total, `own` ones included. That's accepted (ADR 0026 point
   4).

## Alternatives considered
- **Python `re` with the C3a validator:** measured at 0.95 s per search on a pattern that passes it.
  Static checks on a backtracking engine keep losing to new shapes.
- **`regex` module with `timeout=`:** still backtracking, so each slow row burns CPU up to the
  timeout. A wall-clock timeout also makes the same rule match or miss depending on load, so the
  preview count and the applied count can differ.
- **Killable subprocess per evaluation:** bounded, but it brings spawn cost per preview, the same
  load-dependent nondeterminism, and a new process-management surface in the API.
- **Older RE2 wrappers (`pyre2`, `re2` forks):** unmaintained, and some silently fall back to `re`
  for unsupported syntax, which brings backtracking back.
- **A larger `max_mem` instead of the probe:** measured no better at 1,024 characters, and it costs
  memory per compiled rule.
- **Counting DFA states instead of timing:** the binding doesn't expose RE2's DFA statistics.
- **No regex at all:** safe and small, and it's the exit (Consequences). PLAN lists regex, and bank
  descriptions often need one (`AMZN MKTP|AMAZON\.COM`), so we ship it with the engine in.

## Consequences
- A new compiled dependency. `pip-audit` covers it, and Harbor's audit allowlist doesn't need an
  entry. Hard rule 9 is about services, and this is a library, so no service review is needed.
  This ADR records the dependency check the security rule asks for.
- **Watch and exit (owner: Harbor).** The binding's last release is 2025-11-05. The exit applies on
  any of these triggers:
  - a Python bump with no wheel
  - a `pip-audit` finding with no fix
  - 18 months with no release

  The exit: regex rules show `unavailable` and are skipped (point 2), the API stops accepting new
  ones, and an `any_of` literal-list mode replaces the common case (`AMZN MKTP|AMAZON\.COM` becomes
  two literals). No engine is needed for it. `any_of` isn't built in Phase 4.
- Patterns that worked under `re` semantics but use backreferences or lookaround are rejected at
  save. No rules are stored yet, so nothing needs migrating.
- The probe is timing-based, so acceptance near 10 µs can vary between saves. Matching never does.
- The probe is a heuristic over subject families, not a proof. It catches every adversarial shape
  measured so far: counted classes after an unanchored prefix, decoys, escapes, negation, POSIX and
  frequent literals. A new evasion is a corpus addition and a probe change in `rule_regex.py`,
  reviewed by Bulwark. The cap and the 256-character subject bound the damage in the meantime.
