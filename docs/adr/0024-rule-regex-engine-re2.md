# 0024. Rule patterns run on RE2 (google-re2), not Python `re`

- Status: Accepted
- Date: 2026-11-21
- Deciders: Lattice (architect); Keel. Bulwark verdict required on the implementing PR (C3d).

## Context
PLAN 5 lists `desc_regex` among rule conditions, and C3a (`app/domain/rules.py`, PR #53) accepts
a regex mode guarded by a static validator: 128-character patterns, at most one unbounded repeat,
limited alternation, descriptions capped at 1,024 characters. Bulwark showed the validator is not
enough: `A?A?A?A?A*` followed by 117 `A`s and a `B` passes it and takes about 0.95 s for one
search of a 1,024-character description. We reproduced this on 2026-11-21 (0.951 s, CPython 3.12).
Rules run over every live row in a retroactive preview (tens of thousands of rows in the request
path) and on every ingested row, so one saved pattern like that is a denial of service against
the API and the worker. PR #53's gate: no user-written pattern may be saved or run until a
linear-time engine or a killable subprocess is chosen.

The preview and apply contract (PHASE-4 contract, C3e) also needs determinism: the count a
person approves must be the count written. Anything that can match or not match depending on load
breaks that.

## Decision
1. **Engine: `google-re2`, pinned `==1.1.20251105`**, the official Python binding of Google's RE2
   (the `python/` directory of `github.com/google/re2`). RE2 compiles to automata and matches in
   time linear in the subject, with no backtracking. Facts checked against PyPI on 2026-11-21:
   - Licence: BSD-3-Clause (OSI "BSD License" classifier), compatible with AGPL-3.0.
   - Wheels for CPython 3.10 to 3.14, including `manylinux_2_28` **x86_64 and aarch64**, macOS
     and Windows (`win_amd64`, `win_arm64`). About 0.6 MB, no runtime dependencies (abseil is
     linked in). Our image is `python:3.12-slim-bookworm` (glibc 2.36, above 2.28).
   - No `musllinux` wheel: an Alpine image would have to build from source. We don't use Alpine.
   - Measured locally: the slow pattern above, 1,000 searches of the 1,024-character subject,
     about 2 ms in total (the same with `(x+x+)+y`).
2. **One module owns it.** `app/domain/rule_regex.py` is the only importer of `re2`; a test fails
   on `import re2` anywhere else. It exposes `compile_pattern(pattern: str) -> CompiledPattern`
   (`.search(text) -> bool`) and raises `RuleValidationError` with a fixed message.
3. **Options:** `case_sensitive=False`, `never_capture=True`, `log_errors=False` and
   `max_mem = 1 << 20` (1 MiB per compiled pattern). `log_errors=False` matters: by default RE2
   writes the rejected pattern to stderr through abseil logging (observed), and rule text is user
   content that must not reach logs.
4. **Validation becomes simple.** The C3a structural validator is deleted. A pattern is accepted
   when it is 1 to 128 characters, contains no NUL, compiles under the options above, and does
   **not** match the empty string (a pattern that matches `""` matches every description, which is
   a catch-all rule; Ledger rejected those in C3a). Error messages never echo the pattern.
5. **Subjects:** regex conditions search the first 1,024 characters of the normalised description
   (`normalize_description`, upper case and collapsed whitespace). C3a skipped longer descriptions,
   which made a rule silently miss them; truncating is bounded and predictable.
6. **Syntax differences from `re` are documented for people writing rules** (`docs/user/rules.md`,
   C3d): no backreferences, no lookahead or lookbehind, repeat counts at most 1,000, and `\d`,
   `\w`, `\s` and `\b` are ASCII-only (`\b` treats `é` as a non-word character); `\pL` and
   `[[:alpha:]]` are available. Matching is case-insensitive.
7. **Gate.** Until C3d merges, the rules API rejects `mode: "regex"` with `validation_error`, and
   literal modes (`contains`, `starts-with`, `exact`) ship on their own. C3d's tests run Bulwark's
   two slow patterns from PR #53 plus a generated adversarial corpus (nested quantifiers,
   alternation blow-ups, 128-character patterns against 1,024-character subjects) with a budget of
   1,000 searches per pattern in under 1 s on CI.

## Alternatives considered
- **Python `re` with the C3a validator:** measured 0.95 s per search on a pattern that passes it.
  Static checks on a backtracking engine keep losing to new shapes.
- **`regex` module with `timeout=`:** still backtracking, so each slow row burns CPU up to the
  timeout, and a wall-clock timeout makes the same rule match or miss depending on load, so the
  preview count and the applied count can differ.
- **Killable subprocess per evaluation:** bounded, but spawn cost per preview, the same
  load-dependent nondeterminism, and a new process-management surface in the API.
- **Older RE2 wrappers (`pyre2`, `re2` forks):** unmaintained, and some silently fall back to `re`
  for unsupported syntax, which brings backtracking back.
- **No regex at all:** safe and small, and it is the fallback (point 7). PLAN lists regex, and
  bank descriptions often need one (`AMZN MKTP|AMAZON\.COM`), so we ship it when the engine is in.

## Consequences
- A new compiled dependency. `pip-audit` covers it; Harbor's audit allowlist does not need an
  entry. The binding's last release is 2025-11-05; a year without a release is a watch item. If a
  future Python has no wheel or an audit finding has no fix, the fallback in point 7 applies
  (regex rules stay stored but are skipped and flagged, and the API stops accepting new ones).
- Patterns that worked under `re` semantics but use backreferences or lookaround are rejected at
  save. No rules are stored yet, so nothing needs migrating.
- Hard rule 9 is about services, and this is a library, so no service review is needed; this ADR
  records the dependency check the security rule asks for.
