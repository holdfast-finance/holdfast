# Firstchair: driving Codex and Grok from the lead session

Keel drives the Codex CLI (Anvil) and Grok Build (Helm) through `firstchair`, never through their
raw CLIs. This page is the full command reference as of `0.1.0a2`, plus the rules this repo adds.
Check `firstchair --version` and `firstchair <command> --help` when a flag here looks wrong: the
tool is alpha and its interface can change.

## Rules for this repo

1. **Always confine a Codex or Grok worker, with the full hide list.** Pass the flags that
   `scripts/fc-hides.sh` prints: `firstchair delegate run codex ... $(scripts/fc-hides.sh)`. They
   are `--confine` plus `--hide` for the private data directory (ADR 0019), the container sockets
   (`/run/docker.sock`, `/var/run/docker.sock`, the rootless ones under `/run/user/<uid>/`), the
   session bus and keyring sockets (`/run/user/<uid>/bus`, `keyring`, `gnupg`, `gcr`) and the lead
   session's scratch directory. `--confine` alone is not enough: it is a filesystem view under the
   lead's own user and groups, not a user boundary. It hides the project's checkouts and common
   secret stores but no sockets, so without the extra hides a worker could reach the running
   stack through the container socket, or a Git host token through the desktop keyring. Real
   statements and anything derived from them must never reach another vendor's CLI. `--confine`
   needs bubblewrap and refuses to run without it. Never type the flags by hand.
2. **`conduct` cannot confine.** `conduct run` has no `--confine`, `--hide`, `--base`, `--model` or
   `--effort`. On a machine that holds real data, a score may use only the `claude` and `self`
   seats. Fan out Codex and Grok with several `delegate run` jobs in the background instead.
3. **Codex writes, Grok reads.** A Grok write worker is refused unless `--allow-grok-write` is
   passed. Do not pass it. A Grok read worker still has a shell with no permission prompts (only
   its two edit tools are removed), and Codex runs commands too, so rule 1 applies to read jobs
   exactly as it does to write jobs.
4. **Workers never commit, push or merge.** Keel reviews the diff, runs the suite, commits with
   the worker's agent trailer, scans, and lands the PR.
5. **A Codex or Grok review is a second opinion.** It never satisfies `review-gate`. Gate
   sign-offs come from the project's own reviewers, bound to the head SHA.
6. **Verify a worker's test claims.** Codex cannot run database tests or `pnpm` in its sandbox.
   Run `make test-locked` and `make lint` on the applied diff before believing a pass.
7. **One session ID per lead session.** Pass `--session <id>` before the subcommand on every call.
   `collect`, `finish`, `conduct cancel` and `conduct resume` are refused from another session.
8. **Check usage before a sprint** with `firstchair auth usage --refresh`, and pass
   `--rotate-accounts` so each worker takes the account with the most room. Move build and
   second-opinion work to Codex and Grok when the lead's own window is above 70 percent.

## Picking a command

| Need | Command |
|---|---|
| One build task for Codex | `delegate run codex --mode write` |
| One second-opinion review of a PR branch | `delegate run auto --mode read --base origin/<branch>` |
| A plan checked by a second model, nothing written | `run --provider codex --reviewer grok --workspace <dir>` |
| Several independent Claude-only parts, or a part that reads an earlier part's reply | `conduct run` with a score |
| What a write worker changed | `delegate diff <name>` |
| Put a worker's edits on a PR branch worktree | `delegate apply <name> --to <worktree>` |

## Global options

`firstchair [--state-dir DIR] [--session ID] <command>`. State lives in `.firstchair/` in the main
checkout (ignored by Git). The session ID is 1 to 63 characters of `a-z 0-9 . _ -`, or set
`FIRSTCHAIR_SESSION`.

## `delegate`: one bounded task in its own worktree

`firstchair delegate [--repo PATH] run {codex|claude|grok|auto|self} [task]`

| Flag | Effect |
|---|---|
| `--mode read\|write` | Read jobs use a disposable worktree. Write jobs keep branch `delegate/NAME` |
| `--name NAME` | Job name, also the worktree and branch suffix. A name in use is refused |
| `--prompt-file FILE` | Read the task from a file. `-` as the task reads stdin |
| `--lead claude\|codex\|grok` | The lead session's provider (default `claude`); decides what `auto` picks |
| `--base REF` | Start from a branch, tag or commit instead of the checkout's HEAD. Use it to review a PR branch. Does not combine with `--with-changes` |
| `--with-changes` | Copy the checkout's uncommitted and untracked files into the worktree |
| `--confine` | Hide the project's other checkouts, common secret stores and the other providers' files |
| `--hide PATH` | Also hide this absolute path. Repeatable. Implies `--confine` |
| `--model MODEL`, `--effort EFFORT` | Override the worker's model or reasoning effort. Explicit seats only, not `auto` or `self` |
| `--profile NAME` | Use a named sign-in for an explicit worker |
| `--rotate-accounts` | Pick the worker's account from real usage |
| `--timeout SECONDS` | Kill the worker after this long (default 1,800) |
| `--output FILE` | Also write the JSON summary, reply included, to a file only the user can read |
| `--keep` | Keep a read job's worktree |
| `--dry-run` | Show which worker would be chosen, without starting it |
| `--allow-grok-write` | Accept a Grok write worker. Not used here (rule 3) |

Seats: `codex`, `claude` and `grok` start that CLI. `self` starts the lead's own provider as a
separate worker. `auto` never picks the lead: under a Claude lead it picks Grok, then Codex, for a
read task and Codex for a write task.

After the worker returns:

| Command | Effect |
|---|---|
| `delegate list` | Worktrees, branches and each job's status: `running`, `completed`, `failed`, `timeout`, `terminated`, `abandoned` |
| `delegate diff NAME` | The write job's edits as one patch, new files included |
| `delegate apply NAME [--to PATH]` | Stage the patch in a checkout without committing. Changes nothing if it does not apply |
| `delegate collect NAME [-m MSG]` | Commit the edits on the job's own branch |
| `delegate finish NAME [--delete-branch] [--force]` | Remove the worktree and the job record so the name can be reused. `--force` discards uncommitted or unmerged work |

A summary reports `status`, `exit_code`, `elapsed_seconds`, the `reply`, measured token `usage`,
and for a failed worker the last 2,000 characters of stderr. A write job that changed no file
carries a warning.

The usual flow here: create a branch worktree with `scripts/wt-new.sh`, `delegate apply NAME --to
<that worktree>`, read the diff, run the suite, commit with the agent's trailer, then
`delegate finish NAME --force --delete-branch`.

## `conduct`: a score of up to eight parts

`firstchair conduct [--repo PATH] {plan|run|resume|cancel|status}`

A score is a JSON file with `parts` and an optional `limit`. Each part has `name`, `seat`, `mode`
and `task`, and may have `timeout` and `after`.

```json
{
  "limit": 2,
  "parts": [
    {"name": "notes", "seat": "self", "mode": "read", "task": "List the open follow-ups in docs/planning"},
    {"name": "check", "seat": "claude", "mode": "read", "after": "notes",
     "task": "Check each item in the earlier reply against the code"}
  ]
}
```

| Command | Effect |
|---|---|
| `conduct plan --lead L --score FILE` | Resolve seats without starting a worker |
| `conduct run --lead L --name N --score FILE [--limit 1..4] [--with-changes] [--rotate-accounts] [--allow-grok-write]` | Run the score. Parts run one at a time unless a limit is set |
| `conduct status NAME` | The board, plus whether the conductor is `live` or `gone` and each running part's elapsed time |
| `conduct cancel NAME` | Stop the running parts. Refused if no conductor holds the lock |
| `conduct resume --name N --score FILE` | Continue a score whose conductor is gone, skipping completed parts |

- Parts run in waves of `limit`, in input order. After a wave with a failed part, no later wave
  starts.
- `after` passes one earlier part's reply into a follow-up, marked as untrusted and cut at 32,000
  characters. The two parts must fall in different waves.
- The board stores status only, never task text or replies. Replies are in the JSON the command
  prints, so capture stdout.
- Resume needs the same score file. A write part that did not finish and left a worktree blocks
  the resume until it is reviewed and finished. A completed write part is never replayed.

## `run`: plan, review, synthesize, read-only

`firstchair run --provider P [--reviewer R] [--workspace DIR] "task"`. The lead plans, the reviewer
critiques, the lead writes the answer. Providers get read-only tools and no MCP servers. It writes
nothing, so it suits a design question, not a build.

| Flag | Effect |
|---|---|
| `--reviewer codex\|claude\|grok` | Second model for the review stage (default: the lead) |
| `--fallback PROVIDER[:PROFILE]` | Next seat when an earlier one is out of usage. Repeatable |
| `--workspace DIR` | Directory the providers may read. Without it they see an empty temporary directory. Never point it at the private data directory |
| `--profile`, `--reviewer-profile` | Named sign-ins |
| `--rotate-accounts` | Pool every named sign-in and pick by real usage before each stage |
| `--account-switch-percent N` | Move off an account at N percent used (default 90) |
| `--account-token-limit N`, `--account-call-limit N` | Also move off after N tokens or N stages in the window |
| `--account-window S`, `--account-cooldown S` | Look-back window (default 18,000) and rest after a usage failure (default 3,600) |
| `--timeout S`, `--total-timeout S` | Per-stage limit and whole-run budget |
| `--max-input-bytes N`, `--max-output-bytes N` | Request and output size limits |
| `--format text\|json`, `--dry-run` | Output form; show the plan without running |
| `--demo`, `--provider synthetic`, `--config FILE` | Offline demo and its limits file |

Follow-ups: `runs` lists saved runs, `status RUN_ID` reads one, `recover RUN_ID` marks an abandoned
run interrupted, and `resume RUN_ID [--provider P] [--fallback P] [--retry-exhausted]` continues
from the last finished stage.

## `auth` and `doctor`

| Command | Effect |
|---|---|
| `doctor [--offline]` | Find the three CLIs and report whether each version is verified, compatible or unsupported |
| `auth login PROVIDER PROFILE [--device-auth]` | Sign in a named account through the provider's own CLI. The owner runs this |
| `auth check PROVIDER PROFILE [--probe]` | Check a sign-in without printing account details |
| `auth list` | Named profiles |
| `auth usage [--refresh] [--window S]` | Percent used per account and any cooldown. `--refresh` asks each provider now |
| `auth clear-cooldown [ACCOUNT]` | End a rest early |

Real usage is read for Codex and Claude Code. Grok reports none, so firstchair counts its own
calls: use `--account-call-limit` when Grok does a lot of work.

## Gaps to remember

- `conduct` has no confine, base, model or effort options (rule 2).
- `--model` and `--effort` are refused with the `auto` and `self` seats.
- `--confine` hides no sockets and does not change the user or its groups (rule 1). The target
  remains a separate OS user with no container access and a read-only Git host identity for
  these workers; until that exists, the hide list is the control.
- A worker killed together with its conductor can keep running in its own process group. Check
  for it before resuming a score.
