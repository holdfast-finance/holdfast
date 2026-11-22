# Install-everywhere plan (revision 3)

Owner: **Harbor** (drafts), **Keel** (schedules). Decision record: `docs/adr/0037-install-on-every-platform.md`
(Proposed, revision 2; it answers Gadfly's review point by point). Nothing here is built. Sizes: S = 1 run,
M = 2 to 3 runs, L = split before scheduling (ROADMAP units). Prefix `IN`. Each story gets a brief under
`docs/planning/briefs/<ID>.md` when scheduled.

## Where this sits

- **The repository is public** (verified 2026-11-22; `v0.0.0-alpha` published 2026-11-21). No gate in this plan waits for
  "going public".
- **Preemptible, with a pull-forward.** The Q1 finish line is Phase 5 (`ROADMAP.md`). This lane runs when the critical path
  is blocked, except **IN0, IN1, IN-A1, IN-B1 and the owner-run IN-S0**, which Keel should pull forward: they decide whether any
  installer is safe to ship.
- **ADR 0021 dependency, resolved by absorbing it.** A stranger on Windows or macOS sees a usable app only with the local
  ingress (PA-D2: `127.0.0.1:47800`, no certificate warning, so passkeys work) and the `holdfast` wrapper (PA-D6). This plan
  **pulls PA-D2 in as IN-A1 and replaces PA-D6 with IN-A2, IN7, IN10 and IN15**; ADR 0021's PA-D6 row now says so. The rest of
  Phase 8.5 (phone wizard, recover, docs, real-device acceptance) stays where it is. Until IN-A1 merges, any install is a
  labelled "preview" with a certificate warning.
- **Keel decisions (2026-11-22, owner may veto):** Q4 one 5.1 bootstrap file plus a narrow rule 10 amendment (IN-W1, Bulwark-gated); Q5
  `.pkg` only, `.dmg` deferred; Q6 Keel creates the Worker and record with `cf` at IN-E1, deployed from CI on a `v*` tag; Q9 Docker Desktop
  default with Colima documented; Q10 Podman after the first usable run, Raspberry Pi after Podman; Q11 Keel creates the tap repo at the
  Homebrew slice (the plan's IN13), winget later; N1 no channel before an upgrade path; N2 key ring in the state directory, host backups
  include it, encrypted with a user passphrase never stored with the backup; N4 GitHub Issues with templates; N5 only Keel creates `v*` tags.
- **Owner answers (2026-11-22):** macOS ships an unsigned `.pkg` plus the Homebrew and terminal route first, no Apple
  enrolment; Windows signing is SignPath's free open-source tier; the owner has a personal Windows PC and **no Mac**.

## Facts that shape the order (checked 2026-11-22)

- There is no Ubuntu-specific code. The real gaps: install means "clone + make + dev stack", images are `linux/amd64` only,
  nothing but `ubuntu-latest` has run, Windows never has (PHASE-0 blocked item).
- `ubuntu-24.04` runners ship Docker 28.0.4, **below the 28.3.3 floor**; `ubuntu-26.04` (a listed label, no preview badge)
  ships 29.4.2 and Podman 5.7.0. Windows 2025 runners ship Docker 29.7.2 for Windows containers. macOS runners ship no Docker.
- `scripts/gen-keys.py` already warns that a new `FERNET_KEY` cannot decrypt old data. That is why state is separate from code.
- Both `https://holdfastfinance.app/install.sh` and `/install.ps1` return **404** (2026-11-22). Nothing is deployed there.

## Stories

Format: `ID title — size — owner/model — reviewers — depends — acceptance (evidence that can fail)`. Keel's "IN-M2" for the tap is this plan's **IN13**.

### E-I0 First usable run, data safety, and the Ubuntu-only assumptions (do first)

- **IN-S0 Owner-hardware spike on the Windows PC** — S — Harbor/sonnet, run by the owner on the personal PC — Bulwark — a **draft release
  compose built in CI and pulled by digest** (a small slice of IN1's work, `IN1-draft`) — shipped as **one `pwsh` script that prints the result
  table**, and listed in the `docs/STATUS.md` owner queue because the owner runs it by hand. It checks, with Docker Desktop: `docker compose up
  -d --wait` reaches `/api/health` from the draft bundle; uid 10001 can write the documents volume; read-only root works; file sharing does not
  break the Postgres init script; RAM and disk cost is **measured** (feeds the preflight); the **CPU architecture is recorded** (on Windows-on-ARM,
  IN4 comes before this spike); and **a second LAN host cannot reach the published port** (ADR 0021's loopback probe on Docker Desktop for
  Windows). It does **not** run the Docker Desktop reset or purge drill (that deletes every volume on the machine, including unrelated data):
  which actions delete volumes is taken from Docker's docs, and the drill runs only on a machine confirmed to hold no other Docker data (IN-W3).
  The script needs only `pwsh` and Docker on the host, no `python3`, `make` or `git`. Output: a pass/fail table in `docs/ops/`, numbers and counts
  only. **Never on employer equipment** (PLAN Section 0). **It gates IN10 onward only.** IN0, IN1, IN-A1 and IN-B1 do not wait on owner time.
- **IN0 Portability audit as a test** — S — Harbor/sonnet — Bulwark — none — `scripts/check-portability.py` fails CI on: a
  `bash` shebang in a script the installer ships, `sha256sum` or `sed -i` without a BSD fallback in shipped scripts, a host-path
  bind mount in the release compose, a `build:` key in the release compose, a port other than the allowed loopback one. Each
  violation is seeded once in a test and must fail.
- **IN1 Release bundle and release compose** — M — Harbor/sonnet, contract by Lattice — Bulwark, Lattice — IN0 — `make bundle`
  makes `holdfast-<ver>-bundle.tar.gz` and `.zip` with `compose.yaml` pinned `@sha256`, no `build:`, no dev override, **project name
  `holdfast-app`** (never `holdfast`: the dev stack and any clone use that name) and every volume labelled
  `com.holdfastfinance.install=<install-id>`. CI runs `docker compose config`, `scripts/check-compose-ports.py`, and `up -d --wait` to a healthy
  `/api/health` with `docker-compose.dev.yml` and `./backend` deleted first. A second run from a *different directory* finds the **same labelled
  volumes** (a test fails if a second set appears). **Coexistence test (R2-B1): a dev stack (project `holdfast`) and a release install run side by
  side, and `holdfast keys`, `restore` and `--purge` never select, read or delete an unlabelled `holdfast_*` volume.**
- **IN-A1 Local ingress (ADR 0021 PA-D2, pulled in, plus the setup-token rate limit from PA1)** — S — Harbor + Tide/sonnet — Bulwark — IN1 —
  Caddy's local site on `127.0.0.1:47800` (plain HTTP, `Host: localhost:47800` only), setup on the local ingress only, token exchange with
  `replaceState`, the local rate-limit family, **and the setup-token rate-limit scope that ADR 0021 puts in PA1 (a scope keyed on the setup
  token, Tide with Bulwark)**, since "setup on the local ingress only" depends on it. **ADR 0021 status it needs:** none beyond this PR's
  amendment. ADR 0021 stays Proposed. Its §12 now says U-6 and U-7 gate the per-browser support *claim*, not PA-D2's merge, so IN-A1 merges on
  Bulwark's verdict and Keel's merge. Acceptance: ADR 0021's PA-D2 tests, plus Chrome (and Firefox, U-6, and Edge) on the Windows PC and Linux
  creates a passkey at `http://localhost:47800` with no warning. **The Safari half of U-7 needs a Mac (Q3b) and gates only the macOS channel's
  browser-support claim**; until it closes, the macOS docs say "use Chrome or Firefox", one of Bulwark's two ADR 0021 fallbacks.
- **IN-B1 State directory, local backup and restore** — M — Harbor + Tide/sonnet; contract by Lattice — Bulwark — IN1 —
  (a) state directory per OS, separate from code, with the permissions of ADR 0037 §2 (Linux `0700`/`0600`; macOS created by the user's own
  `holdfast open`, never in `.pkg` postinstall, excluded from Time Machine; Windows ACL on the whole directory) **re-checked at every start**,
  holding `.env`, `install.json` (install id, versions, backup folder, last-backup status) and the recovery receipt; (b) **backups live outside
  state** (`~/Documents/Holdfast Backups` or the OS equivalent) and `pg_dump` and documents are **streamed to the host through stdout**, never
  written by a container into a bind mount; (c) scheduled backup on by default on Windows (Task Scheduler) and macOS (launchd), offered on Linux,
  each run writing a **status file** (`ok`, `skipped: runtime stopped`, `failed`) that `holdfast open` and an **in-app banner** show as the age of the
  last good backup (amber 2 days, red 7); (d) a read-only **backup receipt** reaches `api`, and the **first import is refused** without a receipt
  from the last 24 hours; (e) **the archive holds the whole secret set: every secret key in `.env.example` including `IMPORT_ID_KEY`**, encrypted
  by envelope encryption; the **passphrase-wrapped private key lives in every archive's header and in state, never only one**; `holdfast backup
  rekey`; a **periodic passphrase check** in `holdfast open`; the recovery step recommends a generated recovery code; (f) a backup **holds a lock that
  blocks `rotate-keys`**, captures the ring **after the dump with all kids kept**; restore runs ADR 0015's residual scan and **refuses if a kid is
  missing**, and swaps `.env` atomically keeping `.env.prev`. The chosen primitive gets the maintenance and AGPL-compatibility check. Acceptance
  tests (each can fail): wrong passphrase fails, right one reproduces the data; **delete state, restore from an archive and the passphrase alone**;
  the archive's secret set equals `.env.example`'s secret keys; the archive and state contain no passphrase and no plaintext `FERNET_KEY`; the backup
  folder is a host path, never a volume; **rotate in the middle of a backup, then restore**; scheduled task exists after install and is gone after
  `--purge`; a stopped runtime produces `skipped` and an aged banner, not silence. This overlaps Phase 9 (offsite restic), which layers on top;
  Lattice writes the contract so Phase 9 does not redo it.
- **IN2 Named documents volume (no host chown)** — S — Harbor + Tide/sonnet — Bulwark — IN1 — the stack stores and reads a synthetic
  document as uid 10001 on a fresh volume with no `chown` helper and no bind mount; a restart keeps it. (Fallback if Bulwark objects:
  bind mount with `:z` and `:U`; spike IN-L-S2.)
- **IN3 `holdfast keys` with a host shim** — S — Harbor/sonnet — Bulwark — IN1, IN-B1 — the api image's generator writes to **stdout**; the host
  shim creates state's `.env` with the permissions of ADR 0037 §2 (POSIX `umask 077`, Windows an ACL on the directory via `icacls`, asserted in
  Pester). **Refuses to write when state holds `.env` or when volumes carrying this install's label exist.** It fills **every secret key in
  `.env.example`** (a test derives the list from the file, not a hard-coded count; today that includes `IMPORT_ID_KEY`). A canary test greps the job
  log for the generated values. The **recovery step** has the user set the backup passphrase (or accept a generated recovery code) and confirm they
  stored it, and says plainly that a lost one makes backups unrecoverable. `make keys` and `dev.ps1 keys` keep writing the repo-root `.env` for
  contributors; a parity test asserts they and `holdfast keys` **share the generator**, not the destination.
- **IN4 Multi-arch images** — M — Harbor/sonnet — Bulwark (`release.yml` is protected) — IN1 — the images job builds
  `linux/amd64,linux/arm64`; `docker buildx imagetools inspect` lists both; the arm64 image starts the stack on `ubuntu-24.04-arm`
  (free on the public repo now).
- **IN5 Docker floor check, shared** — S — Harbor/sonnet — Bulwark — none — one function (`scripts/lib/docker-floor.sh` plus a
  PowerShell twin) compares `docker version` numerically. A shared JSON table: 28.0.4 refused, 28.3.3 and 29.9.3 accepted,
  `24.0.7`, `20.10.24`, garbage and empty refused with a named message. Both languages run the same table and a test fails if
  they diverge.
- **IN6 Windows host without `make` or Python** — S — Harbor/sonnet — none — IN3 — `dev.ps1 keys/up/down/ports` no longer need
  `python3`/`python` on the host. PSScriptAnalyzer clean and `help` runs in a `pwsh` container.
- **IN-A2 The `holdfast` wrapper (replaces ADR 0021 PA-D6)** — M — Harbor + Tide/sonnet — Bulwark — IN-B1, IN3, IN5 — `up`, `down`,
  `open`, `update`, `backup`, `restore`, `export`, `recover` below the floor as ADR 0021 says, `keys`, `uninstall`, `phone`. POSIX
  `sh` and a `holdfast.ps1` twin; a parity test lists both command sets and fails on a difference.
- **IN-O1 `holdfast open` launcher on every OS** — S — Harbor/sonnet — none — IN-A2 — starts the runtime if stopped (Docker Desktop;
  Colima via `brew services`), waits for the engine with a printed reason, runs `up`, opens the browser. Windows gets a Start-menu
  shortcut, macOS gets `Holdfast.app`, Linux a `.desktop` entry. Test: with a fake runtime that starts late, the launcher waits and
  then succeeds; with one that never starts, it exits non-zero with a named message within a timeout.

### E-I1 Release integrity (before any channel opens)

- **IN-T1 Tag control and release guard (S2, R2-S4)** — S — Harbor/sonnet — Bulwark — none — `release.yml` refuses unless the tagged commit is
  reachable from `main` and its PR head had a green `ci-required`; signing runs in a protected environment. **Tag control is a process rule today
  (Keel decision N5: only Keel creates `v*` tags):** every agent acts as the owner's GitHub account, so a tag ruleset has no distinct identity to
  allow, and a required reviewer is either the owner (a gate on every release) or the same account approving itself. The ruleset and "prevent
  self-review" become enforceable when the **separate agent identity from ADR 0036** exists; this slice records that dependency and adds the ruleset
  then. Test: a tag on an unmerged branch commit fails the `verify` job (this works today).
- **IN-CG Channel gate (R2-S5)** — S — Harbor/sonnet — Bulwark — IN15, IN-T1 — `release.yml` gets a `channel-gate` job and every channel job (stub,
  `.exe`, `.pkg`, formula bump, `get.` deploy) `needs` it. It fails unless the IN15 upgrade-path test job **passed on the release commit**.
  Test: a release candidate with a red or missing IN15 result publishes no channel artifact (the job log shows the refusal), and the README is not
  consulted.
- **IN-T2 Signed `CHANNEL` and minimum version (S3)** — S — Harbor/sonnet — Bulwark — IN-T1 — `release.yml` signs `SHA256SUMS` and a
  one-line `CHANNEL` with Sigstore; the stub embeds a minimum version. Test: a stale but validly signed old release is refused by
  a fresh install (freeze test); a `latest.json` that names a version `CHANNEL` does not is refused.

### E-I2 Endpoints on our domain (Cloudflare, plan only until the owner says go)

- **IN-E0 Read-only inventory** — done (2026-11-22): `cf` signed in; one assets-only Holdfast Worker exists (`cloud.` preview); R2 not
  enabled (`[10042]`); no Pages projects; the account also holds unrelated Workers; the apex returns 404 for `/install.sh` and
  `/install.ps1`. No change made. `cf` is the default tool; `npx wrangler` works on the same account and is used only if a Wrangler
  config is ever committed.
- **IN-E1 `get.holdfastfinance.app` Worker** — S — Harbor/sonnet; Keel creates the Worker and DNS record with `cf` (Q6) — Bulwark — IN7, IN-T1 —
  **deployed from CI on a `v*` tag only**, using a Cloudflare API token scoped to this one Worker, stored as a secret of the protected release
  environment (it can swap the stub, so it is the credential Q7 is about). The deploy job reuses IN-T1's tag guard. `infra/get-site/`
  holds the stubs, `_headers`, `_redirects`, `latest.json`. After deploy: `curl -sI` shows `text/plain`, `nosniff`, `no-cache`; `/latest`
  302s to the GitHub Release; `workers.dev` and preview URLs are off; the Worker has no script and no bindings; a CAA record exists
  for the CA in use; the deployed `install.sh` hash equals the Release asset's (test fails otherwise). **The install page lists the
  GitHub-URL one-liner and the stub hash from the Release notes.**
- **IN-E2 Deploy step in RELEASES.md** — S — Beacon + Harbor — Keel — IN-E1 — RELEASES.md describes the CI deploy on a `v*` tag, the manual `cf`
  fallback, token rotation, and the hash-comparison step.

### E-I3 Linux and other Unix

- **IN7 `install.sh` stub and core** — M — Harbor/sonnet — Bulwark, Gadfly — IN1, IN-A2, IN-T2 — shellcheck clean under `sh` and `dash`;
  `--dry-run` prints the plan with no side effects; tampered hash exit 20, bad Sigstore bundle exit 21, downgrade or below-minimum
  exit 22, all leaving nothing on disk; no `--insecure` flag (test greps); under 80 lines (test counts); the body is a function
  called on the last line (test truncates the stream at every byte offset in a corpus and runs nothing); PATH check with consent
  prompt for the rc line.
- **IN8 Distro detection and `--install-docker`** — M — Harbor/sonnet — Bulwark — IN7 — table-driven tests of `/etc/os-release` fixtures
  (Ubuntu 22.04/24.04/26.04, Debian 12/13, Fedora 43/44, Rocky 9, Raspberry Pi OS, Arch, Alpine, openSUSE): the right action or a
  named "unsupported" message. **Only dry-run and fixtures here**; the real `--install-docker` run happens on a clean VM in IN9.
- **IN9 Linux acceptance on real distros** — M — Sounding/sonnet — Keel — IN7, IN8 — Fedora 43 (the owner's box, **run by hand, not
  as a runner**), and clean Debian 13 and Ubuntu 24.04 VMs each run the one-liner to a healthy `/api/health` and create a household
  and passkey; the Ubuntu 24.04 VM proves `--install-docker` from a clean image. Counts and pass/fail only.
- **IN-L-S1 Spike: Podman** — S — Harbor/sonnet — Bulwark — IN1 — `docker compose` against `podman.sock` (rootless) on `ubuntu-26.04` and
  Fedora 43: `up -d --wait`, merge tags, cap_drop, read-only, internal networks, `:z`, loopback bind. A yes/no table in `docs/ops/`.
- **IN-L-S2 Spike: bind mount vs named volume on SELinux** — S — Harbor/sonnet — none — IN2 — evidence from Fedora 43 (Docker, no SELinux
  option) and Podman (enforcing).

### E-I4 Windows

- **IN10 `install.ps1` stub and core** — M — Harbor/sonnet — Bulwark, Gadfly — IN1, IN-A2, IN-T2, IN5, IN6 (the rule 10 amendment is IN-W1) — Pester: tampered hash 20,
  bad bundle 21, downgrade 22; `-DryRun`; PSScriptAnalyzer clean; **under 80 lines**; a test proves it only gets `pwsh` and hands off
  (reads no secret, writes only to a temp directory, runs no repo content before verification). **A CI job runs the bootstrap under
  `powershell.exe` 5.1 on `windows-2025`**, forces TLS 1.2, and takes the hash-pinned PowerShell MSI path when `winget` is absent
  (simulated). A **preflight** reports build, edition, firmware virtualization, RAM, disk, winget, WSL and `pwsh` *before any download*.
  Pester covers the paranoid path including `Unblock-File`. If the amendment is refused, the stub is the two-step form with the same acceptance.
- **IN11 Inno Setup `.exe`** — M — Harbor/sonnet — Bulwark — IN10, IN-O1 — `windows-2025` builds it (Inno Setup is preinstalled), installs
  silently to a temp per-user dir, checks files, the Start-menu shortcut and the Apps entry, uninstalls, and asserts the code directory
  is gone, **the state directory is untouched**, and nothing under `%ProgramFiles%` was written. `/VERYSILENT` never installs Docker.
  The build fails if `[UninstallDelete]` names any state path.
- **IN-W-S1 Spike: WSL 2 on a hosted runner** — S — Harbor/sonnet — none — IN1 — **assume the answer is no** and plan the owner-hardware
  path already (IN-S0, IN-W3): hosted Windows runners do not run WSL 2 or Linux containers in practice (Gadfly S8). The spike runs once
  to record the evidence; if it unexpectedly passes, a hosted stack test is added.
- **IN-W1 Reboot-resume, WSL path, and the rule 10 amendment** — M — Harbor/sonnet — Bulwark (CLAUDE.md change) — IN11 — (a) on the owner's personal PC
  (or a clean Windows VM): no WSL, no Docker → the installer elevates once, reboots, resumes, reaches a healthy stack, with a log of steps and
  exit codes; (b) **the same slice proposes the narrow CLAUDE.md hard rule 10 amendment** (Keel decision Q4): exactly one PowerShell 5.1 bootstrap
  file, `installer/install.ps1`, whose only job is to install or locate `pwsh` 7 and hand over. Acceptance for (b): Bulwark signs off the text, and
  the IN10 test that the file does nothing else fails if the file gains any other behaviour. If Bulwark or the owner rejects it, IN10 falls back to
  the two-step form.
- **IN-W2 SignPath signing** — M — Harbor/sonnet — Bulwark — IN11 — apply to SignPath Foundation, **asking whether `v0.0.0-alpha` meets
  "released"** and stating that no Cloud-only code ships in a signed file; then the signing job runs in the protected environment on
  tag jobs. `Get-AuthenticodeSignature` in CI is `Valid` with the right signer; a test fails if the signed file list differs from an
  allowlist or if an unsigned build is attached to a Release that claims signed. Until approved the `.exe` is an unsigned preview.
- **IN-W3 Windows acceptance** — M — Sounding/sonnet — Keel — IN-W1, IN-A1, IN-B1 — the PHASE-0 blocked item closes: the installer and
  `dev.ps1 keys/up/migrate` run on the owner's personal PC with Docker Desktop; passkey works in Chrome and Edge; a Docker Desktop reset
  does **not** lose data because the host-side backup restores it (the B4 drill, run on a synthetic household).
- **IN-W4 winget manifest** — S — Harbor/sonnet — none — IN-W2 — `winget validate` passes; a test submission to `microsoft/winget-pkgs` is
  accepted or the reason recorded.

### E-I5 macOS (no Mac available: see "macOS without a Mac" below)

- **IN12 macOS path of `install.sh`** — M — Harbor/sonnet — Bulwark — IN7 — runs under `/bin/sh` on a macOS arm64 runner with `--dry-run`
  and fake `docker` shims; uses `shasum -a 256`; no GNU-only flag (shellcheck plus BSD tools); asserts a `curl`-downloaded file has no
  quarantine attribute (settles an UNVERIFIED); PATH check for zsh.
- **IN13 Homebrew tap formula** — S — Harbor/sonnet — Bulwark — IN12 — Keel creates `holdfast-finance/homebrew-tap` when this slice is built (Q11) — `brew install --build-from-source` and `brew test` pass from our
  tap on a macOS runner; the formula pins URL and SHA256; `brew audit --strict` is clean. (The runner has no Docker, so the test is the
  formula and the script, not the stack.)
- **IN14 Unsigned `.pkg` (owner decision 1)** — M — Harbor/sonnet — Bulwark — IN12, IN-O1 — `pkgbuild`/`productbuild` job on a macOS runner;
  the `.pkg` installs `holdfast` and `Holdfast.app` into a temp root (`pkgutil --expand` and a file-list assertion); state path is not in
  the payload; it is attached to the Release **labelled "unsigned preview"** with the checksum and `cosign verify-blob` lines in the
  notes. A test fails if a Release attaches a `.pkg` without the preview label.
- **IN-M-S1 Spike: Docker on a macOS runner** — S — Harbor/sonnet — none — IN1 — records whether Colima runs on `macos-15-intel` or
  `macos-26-intel` (Intel retirement date UNVERIFIED) and expects "no" on Apple Silicon. Not a dependency of anything.
- **IN-M3 macOS stack acceptance** — M — Sounding/sonnet — Keel — IN12, IN14, IN-A1, IN-B1, **Q3b** — **blocked until a Mac exists.** On an
  Apple-silicon Mac with Docker Desktop, then Colima: the one-liner reaches a healthy arm64 stack (`docker image inspect` shows `arm64`);
  passkeys in Safari (ADR 0021 U-7) and Chrome; the loopback probe from a second host does not answer; the Docker Desktop reset drill passes.
- **IN-M4 `.dmg` wrapper** — S — Harbor/sonnet — none — IN14 — **deferred** (Keel decision Q5: `.pkg` only for now).
- **IN-M5 Notarized `.pkg`** — M — Harbor/sonnet — Bulwark — IN14, Apple enrolment — **deferred by owner decision.** Listed so the work is not lost.

### E-I6 Update, uninstall, phone access, docs

- **IN15 `update`, `restore`, `uninstall` safety** — L split into M + M — Harbor + Tide/sonnet — Bulwark, Ledger (money data) — IN-A2, IN-B1, IN7, IN-T2 —
  (a) update from N to N+1 on a synthetic household: backup first, **prompting for the passphrase and verified by a scratch restore with it**, refuses to
  continue if the backup or the verification fails (test breaks the backup and sees the refusal; scheduled backups are reported "written, not verified"); stops `api` and `worker` before migrating; keeps N-1 bundle and digests;
  (b) **a migration fails halfway, `holdfast restore` runs on N-1, and the data is byte-identical** (hash of dump before and after);
  (c) Postgres 16 to 17 goes through dump and restore with a test; (d) downgrade and below-minimum refused; (e) **`--purge` never deletes the backup folder without a second typed `--purge-backups`; a purge with a dev stack on the machine touches nothing unlabelled;
  delete state and restore from an archive plus the passphrase alone;** **uninstall keeps state
  and volumes; install, create data, uninstall, reinstall, and an old encrypted row still decrypts and the old Postgres password still
  works**; `--purge` needs the typed household name; neither touches Docker or Tailscale.
- **IN16 Phone access per platform** — S — Harbor/sonnet — Bulwark — IN-A2, IN-W3, IN-M3 — the sidecar overlay starts on Linux and Docker
  Desktop (Windows now, macOS when available) with ADR 0021's serve-config test; `holdfast phone off` returns to loopback only.
- **IN17 Install docs, release notes, support** — S — Beacon/sonnet — Harbor — IN7 — `docs/setup/install.md` per OS: the one-liners (both
  origins), the paranoid path, verify commands, the "unsigned preview" banner, where data lives, how to back up, uninstall. GitHub Issues templates (N4, Keel decision)
  ask for a log with a redaction warning. A link-check job passes and a test asserts every command in the page appears in a CI job.

## macOS without a Mac

Apple's software licence allows macOS in a VM **only on Apple-branded hardware**, so a macOS VM on this Fedora laptop or on the Windows
PC is not an option, and community macOS-on-QEMU images on other hardware are the same breach (UNVERIFIED by me: the licence PDF could
not be machine-read here; confirm before anyone tries). Plan:
1. **Now, on GitHub's macOS runners** (free and unlimited on a public repo; 5 concurrent macOS jobs on the Free plan): script logic with
   fake `docker`, `pkgbuild` and `productbuild`, the formula audit, the no-quarantine check. **No Docker there.**
2. **Later, for the Docker Desktop path and Safari (U-7):** a **rented Apple-silicon host** or a **borrowed Mac** (owner question Q3b). A
   rented host is real Apple hardware, so Docker Desktop runs normally. Cost UNVERIFIED (hourly and monthly rentals exist; get quotes).
3. Until then macOS is documented as "script- and package-tested, stack unverified".

## CI matrix (public repo)

Runner facts read 2026-11-22 (https://docs.github.com/en/actions/reference/runners/github-hosted-runners and
`/concepts/runners/github-hosted-runners`: "free and unlimited on public repositories"; `/reference/limits`: Free plan 20 concurrent
jobs, 5 macOS; runner-images READMEs).

| Job | Runner | What it proves | What it cannot prove |
|---|---|---|---|
| `install-linux-new` | `ubuntu-26.04` (Docker 29.4.2, Podman 5.7.0) | One-liner against a local bundle: verify, keys, `up`, health, port check, uninstall-keeps-state, reinstall-decrypts. Podman spike | Package managers other than apt |
| `install-linux-floor` | `ubuntu-24.04` (Docker 28.0.4) | **Only the refusal below the floor.** (It ships Moby, not docker-ce, so a real `--install-docker` here tests a package conflict, not a user's machine; that run is on a clean VM in IN9) | A user's clean-machine Docker install |
| `install-linux-arm64` | `ubuntu-24.04-arm` | arm64 images start | Pi memory limits, 32-bit OS |
| `install-distro-fixtures` | `ubuntu-latest` + `fedora:43`, `debian:13`, `rockylinux:9`, `alpine`, `archlinux` containers | `/etc/os-release` detection, `--dry-run` plans, script syntax per `sh` | A real daemon on that distro |
| `install-windows-static` | `windows-2025` | Pester, PSScriptAnalyzer, Inno build, silent install and uninstall (state untouched), 5.1 bootstrap job, signature check | **The stack** (Windows containers only; Linux containers and WSL 2 assumed unavailable) |
| `install-windows-arm` | `windows-11-arm` | Scripts and installer on ARM64 Windows | The stack |
| `install-macos-static` | macOS arm64 (`macos-latest`/`macos-26`) | `/bin/sh` 3.2 and BSD tools, brew formula, `pkgbuild`, no-quarantine | **The stack**, Gatekeeper's real double-click |
| `install-macos-intel` | `macos-15-intel` / `macos-26-intel` (retirement date UNVERIFIED) | Optional Colima spike only | Apple Silicon behaviour |

**What cannot be tested on hosted runners, and who covers it:** Docker Desktop on Windows (the owner's personal PC, by hand: IN-S0, IN-W3);
Docker Desktop on macOS (Q3b); the WSL 2 reboot and SmartScreen and Gatekeeper dialogs (manual acceptance); Fedora, Debian and Rocky
with their own daemons (owner VMs, IN9); LAN reachability of published ports from a second host (acceptance runs).

**No self-hosted runner on any machine that holds real data (B6).** This laptop is the owner's long-term host with real statements in a
private directory, and on a public repo a self-hosted runner executes pull-request-controlled code. ADR 0013 §5 already limits that to
same-repo branches and demands its own ADR. So: **no self-hosted runner on the owner's laptop or the personal Windows PC.** Owner-hardware
tests are run **by hand** (a person or agent runs a stated command list and reports counts), not by a registered runner. A runner on a
**dedicated machine or an ephemeral VM**, with no fork triggers, needs its own ADR and a Bulwark verdict **before** it is registered.

**Budget (replaces the private-repo arithmetic).** Standard hosted runners are free and unlimited on a public repo. The limits that remain
are concurrency (20 jobs, 5 macOS) and the paid *larger* runners, which we do not use. So path filters and nightly schedules exist for
**latency and noise**, not cost: the installer matrix runs on path-matching PRs (`scripts/install*`, `scripts/lib/**`, `installer/**`,
`infra/get-site/**`, `release.yml`), nightly on `main`, and on release-candidate tags. ADR 0013's private-repo numbers are stale for standard
runners and should be amended by Keel or Harbor.

## Slice order (revised)

1. **IN0, IN1** (and `IN1-draft`, the CI-built draft bundle). **IN-S0**, the owner-hardware spike on the Windows PC, runs as soon as the draft bundle
   exists, in parallel with step 2. It gates **IN10 onward only**, so nothing in steps 1 to 3 waits on owner time.
2. In parallel **IN-A1** (local ingress), **IN-B1** (state and backup), **IN3**, **IN5**, then **IN2**, **IN4**, **IN6**.
   After this a Windows user can reach a usable app and cannot lose data silently.
3. **IN-A2** (wrapper) and **IN-O1** (launcher), then **IN-T1 and IN-T2** (release guard, `CHANNEL`).
4. **IN10, IN11, IN-W1, IN-W2** (Windows stub, `.exe`, reboot path plus the rule 10 amendment, SignPath), then **IN-W3** acceptance on the personal PC.
5. **IN7, IN8, IN9** (Linux script, distros, acceptance). Cheap, so it may run in parallel with step 4 when a window is free. **Podman (IN-L-S1)
   comes after the first usable run, and Raspberry Pi (arm64 acceptance) after Podman** (Q10).
6. **IN12, IN13, IN14** (macOS script, tap, unsigned `.pkg`), CI-only.
7. **IN15** (backup-first update, restore, reinstall-decrypts), then **IN-CG** (the CI channel gate), then **IN-E1/E2** (endpoints), then **IN17** (docs).
   **No installer channel is published unless IN15 passed on the release commit: a CI check, not a README word (N1).**
8. After that: IN16, IN-W4, IN-L-S1, IN-L-S2, IN-W-S1, IN-M-S1 (spikes and extras), **IN-M3** when a Mac exists, IN-M4 and IN-M5 on request.

## Owner questions

**Answered by the owner 2026-11-22:** macOS unsigned `.pkg` plus Homebrew and terminal first, no Apple enrolment; Windows signing via SignPath; test
hardware is a personal Windows PC and no Mac.

**Decided by Keel 2026-11-22, owner may veto** (technical and $0, so Keel's under full autonomy):

| # | Decision | Where it lands |
|---|---|---|
| Q4 | Yes: one Windows PowerShell 5.1 bootstrap file whose only job is to install or locate `pwsh` 7 and hand over; a narrow CLAUDE.md hard rule 10 amendment is proposed in the same slice, Bulwark-gated | IN-W1, IN10 |
| Q5 | `.pkg` only for now; `.dmg` deferred | IN14, IN-M4 |
| Q6 | Keel creates the `holdfast-get` Worker and the `get.` record with `cf` when IN-E1 is built; deployed from CI on a `v*` tag only (scoped token in the protected environment) | IN-E1, IN-E2 |
| Q9 | Docker Desktop is the default on Windows and macOS (the installer's licence line says it is free for personal use); Colima documented for macOS | IN10, IN12, IN17 |
| Q10 | Podman after the first usable run; Raspberry Pi (arm64) after Podman | IN-L-S1, slice order |
| Q11 | Keel creates the tap repo in the org when the Homebrew slice is built; winget later | IN13, IN-W4 |
| N1 | No channel (stub, `.exe`, `.pkg`, tap) before an upgrade path exists, enforced by a `channel-gate` CI job tied to the IN15 test on the release commit | IN-CG, IN15 |
| N2 | Key ring in the per-OS state directory; host backups include the whole secret set (including `IMPORT_ID_KEY`), encrypted with a passphrase set at first run (a printed recovery code recommended) and never stored with the backup; the wrapped key sits in every archive and in state | IN3, IN-B1, IN15 |
| N4 | GitHub Issues with templates (redaction warning) | IN17 |
| N5 | Only Keel creates `v*` tags: a **process rule today**, enforceable by ruleset once ADR 0036's separate agent identity exists (all agents share the owner's GitHub account now) | IN-T1 |

**Still open, owner-only:**

| # | Question | Cost | Unlocks |
|---|---|---|---|
| Q3b | A **Mac for Docker Desktop and Safari testing**: borrow one, or rent an Apple-silicon host for a few acceptance sessions? (No macOS VM on non-Apple hardware: Apple's licence.) | A rental, price UNVERIFIED (get quotes). A borrowed Mac: $0 | `IN-M3`, ADR 0021 U-7, the macOS phone-access check. Before it, macOS is "script- and package-tested, stack unverified". |
| Q7 | Move Holdfast to its **own Cloudflare account** (this one also holds unrelated projects), with hardware-key 2FA, a **CAA record pinned to the CA in use**, and DNSSEC kept on? Cloudflare is the code-execution root for every `get.` one-liner, and Q6 puts a deploy token there. | $0 (registrar at cost unchanged); a zone move has a short DNS-gap risk | A smaller blast radius for the one place a swapped stub could come from. The GitHub-URL one-liner needs no answer. |
| N3 | **Who is the self-host `.exe` for?** BRAND frames self-host as builder-to-builder. Should a non-technical person who has never heard of Docker be pointed at Holdfast Cloud instead of a Docker Desktop install? | $0 | Installer copy, how much hand-holding the preflight and launcher carry, docs tone. |

Removed earlier: Apple enrolment (deferred by owner decision, `IN-M5` waits), SignPath versus Azure (answered), the self-hosted-runner option for test
machines (forbidden), and public-repo timing (moot: the repo is public).
