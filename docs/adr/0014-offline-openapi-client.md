# 0014. Offline OpenAPI client and decimal strings

- Status: Accepted
- Date: 2026-11-19
- Deciders: Lattice (architect); Keel

## Context
Story F3 generates the web client from the shipping OpenAPI contract so a backend change fails
typecheck until the client is updated (PLAN 11.1, ROADMAP F3). The dump has to be the same on a
configured laptop as in CI. `Settings()` reads the process environment and dotenv files
(`env_file` is `../.env` and `.env`). `Settings(_env_file=None)` still constructs a dotenv source
and still reads `os.environ`, so `APP_ENV=test` with `E2E_HOOKS=true` would mount the e2e routes
into the generated client.

Money is `Decimal` end to end, and JSON amounts are strings (money rule). Pydantic's validation
schema for a bare `Decimal` is `anyOf` of a JSON number and a string. `openapi-typescript` would
then type the field as `number | string`, which invites float arithmetic in the client. Phase 1
has no money fields yet; the type has to be in place before the first one lands.

CI runs backend pytest and the web typecheck in separate jobs. Neither job has both `uv` and
`pnpm`. Harbor owns `.github/workflows` (F7b) and is editing them on another branch.

## Decision
`make api-types` builds the app with `Settings.model_construct()` (declared defaults only), prints
canonical JSON (`sort_keys`, indent 2, trailing newline), and writes `web/src/api/schema.d.ts`.
The first line is `// holdfast-schema-sha256:` plus the sha256 of those exact bytes, then the
`openapi-typescript` output. The marker says schema, not openapi: gitleaks treats the letters
"api" before a colon and a hex digest as a generic API key. The hand-written `web/src/api/types.ts`
is deleted. `contracts.ts`
aliases generated schemas and keeps `ERROR_CODES`, which are client branches and not an OpenAPI
enum. The HTTP client is `openapi-fetch`. Both libraries are the ones PLAN 11.1 already names,
pinned exactly, MIT. They are not a new service.

Drift is two checks, and neither edits a workflow:

- `tests/test_openapi_export.py` hashes a fresh dump and compares it to the header. CI already
  runs pytest, so a schema change fails the backend job until `make api-types` is re-run.
- `make api-types-check` regenerates the whole file and diffs it. `make lint` depends on that
  target, so a local lint fails when the generated TypeScript drifted, not only the hash.

API money, quantities and prices use `DecimalStr` in `app.schemas.common`: accept a string or a
`Decimal`, reject JSON numbers, serialize with `format(value, "f")`, and publish a string JSON
schema. A sentinel in the `Annotated` metadata lets a model walk tell `DecimalStr` from a bare
`Decimal` (`model_fields` unwraps `Annotated`). The shipping document may not contain
`"type": "number"`. Integers (limits, offsets) stay integers. Web arithmetic stays on `decimal.js`.

## Alternatives considered
- Fetch a live `/openapi.json`: needs the stack, and a developer's env can change the routes.
- `Settings(_env_file=None)`: still reads the environment and constructs dotenv.
- Commit a second `openapi.json`: two artifacts to update; the hash header is the fingerprint.
- Put the generator diff in CI: no job has both toolchains, and Harbor owns those files. The
  pytest hash is the check CI actually runs.
- Generate zod from the schema: the roundtable declined it. Response shapes come from
  `schema.d.ts`; forms keep their own zod.
- Leave bare `Decimal`: the input schema is a JSON number, which breaks the money rule.

## Consequences
- Changing a route or schema fails pytest until `make api-types`. After regeneration, `tsc` fails
  at every call site that still uses the old shape. That is the F3 acceptance check.
- New money fields are `DecimalStr`. A non-money JSON number fails the document test on purpose
  until someone extends the rule.
- `scripts/dev.ps1 lint` does not run the generator diff. The hash test covers Windows when pytest
  runs. Adding the diff to the PowerShell lint target is Harbor's, with the scripts.
- `schema.d.ts` is generated and is ignored by prettier and eslint. It is committed so the web job
  can typecheck without the Python toolchain.
