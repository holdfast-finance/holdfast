# Install-everywhere plan (revision 2)

Owner: **Harbor** (drafts), **Keel** (schedules). Decision record: `docs/adr/0037-install-on-every-platform.md`
(Proposed, revision 2; it answers Gadfly's review point by point). Nothing here is built. Sizes: S = 1 run,
M = 2 to 3 runs, L = split before scheduling (ROADMAP units). Prefix `IN`. Each story gets a brief under
`docs/planning/briefs/<ID>.md` when scheduled.

## Where this sits

- **The repository is public** (verified 2026-11-22; `v0.0.0-alpha` published 2026-11-21). No gate in this plan waits for
  "going public".
- **Preemptible, with a pull-forward.** The Q1 finish line is Phase 5 (`ROADMAP.md`). This lane runs when the critical path
  is blocked, except **IN-S0, IN0, IN1, IN-A1 and IN-B1**, which Keel should pull forward: they decide whether any
  installer is safe to ship.
- **ADR 0021 dependency, resolved by absorbing it.** A stranger on Windows or macOS sees a usable app only with the local
  ingress (PA-D2: `127.0.0.1:47800`, no certificate warning, so passkeys work) and the `holdfast` wrapper (PA-D6). This plan
  **pulls PA-D2 in as IN-A1 and replaces PA-D6 with IN-A2, IN7, IN10 and IN15**; ADR 0021's PA-D6 row now says so. The rest of
  Phase 8.5 (phone wizard, recover, docs, real-device acceptance) stays where it is. Until IN-A1 merges, any install is a
  labelled "preview" with a certificate warning.
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

Format: `ID title — size — owner/model — reviewers — depends — acceptance (evidence that can fail)`.

### E-I0 First usable run, data safety, and the Ubuntu-only assumptions (do first)

- **IN-S0 Owner-hardware spike on the Windows PC** — S — Harbor/sonnet, run by the owner on the personal PC — Bulwark — none —
  with Docker Desktop installed, the *current* release compose (a throw-away bundle from IN1's draft, or the dev stack) is run
  once: `docker compose up -d --wait` reaches `/api/health`; uid 10001 can write the documents volume; read-only root works;
  file sharing does not break the Postgres init script; RAM and disk cost is **measured and recorded** (feeds the preflight);
  and **a second LAN host cannot reach the published port** (ADR 0021's loopback probe on Docker Desktop for Windows). Also
  records which Docker Desktop reset/purge actions delete the volumes (B4). Output: a pass/fail table in `docs/ops/`, counts
  and numbers only. **Never on employer equipment** (PLAN Section 0). Fails if any of those checks is red.
- **IN0 Portability audit as a test** — S — Harbor/sonnet — Bulwark — none — `scripts/check-portability.py` fails CI on: a
  `bash` shebang in a script the installer ships, `sha256sum` or `sed -i` without a BSD fallback in shipped scripts, a host-path
  bind mount in the release compose, a `build:` key in the release compose, a port other than the allowed loopback one. Each
  violation is seeded once in a test and must fail.
- **IN1 Release bundle and release compose** — M — Harbor/sonnet, contract by Lattice — Bulwark, Lattice — IN0 — `make bundle`
  makes `holdfast-<ver>-bundle.tar.gz` and `.zip` with `compose.yaml` pinned `@sha256`, no `build:`, no dev override, project
  name pinned to `holdfast`. CI runs `docker compose config`, `scripts/check-compose-ports.py`, and `up -d --wait` to a healthy
  `/api/health` with `docker-compose.dev.yml` and `./backend` deleted first. A second run from a *different directory* finds the
  **same named volumes** (a test fails if a second set appears).
- **IN-A1 Local ingress (ADR 0021 PA-D2, pulled in)** — S — Harbor + Tide/sonnet — Bulwark — IN1; gates U-6 and U-7 — Caddy's local
  site on `127.0.0.1:47800` (plain HTTP, `Host: localhost:47800` only), setup on the local ingress only, token exchange with
  `replaceState`, the local rate-limit family, as ADR 0021 specifies. Acceptance: ADR 0021's PA-D2 tests, plus Chrome on the
  Windows PC creates a passkey at `http://localhost:47800` with no warning. **U-7 (Safari and `Secure` cookies on
  `http://localhost`) needs a Mac and stays open until Q3b is answered;** Bulwark's fallback options in ADR 0021 apply meanwhile.
- **IN-B1 State directory, local backup and restore** — M — Harbor + Tide/sonnet; contract by Lattice — Bulwark — IN1 —
  (a) state directory per OS separate from code (ADR 0037 §2) holding `.env`, `install.json`, `backups/`; (b) `holdfast backup`
  and `holdfast restore` write and read a dump plus documents on the **host filesystem**; (c) scheduled backup on by default on
  Windows (Task Scheduler) and macOS (launchd), offered on Linux; (d) a backup runs before the first import. Acceptance: the
  backup directory is a host path, never a volume (test); restore into a scratch container reproduces row counts and the
  documents' hashes; scheduled task exists after install and is gone after `--purge`. This overlaps Phase 9 (offsite restic),
  which layers on top; Lattice writes the contract so Phase 9 does not redo it.
- **IN2 Named documents volume (no host chown)** — S — Harbor + Tide/sonnet — Bulwark — IN1 — the stack stores and reads a synthetic
  document as uid 10001 on a fresh volume with no `chown` helper and no bind mount; a restart keeps it. (Fallback if Bulwark objects:
  bind mount with `:z` and `:U`; spike IN-L-S2.)
- **IN3 `holdfast keys` with a host shim** — S — Harbor/sonnet — Bulwark — IN1, IN-B1 — the api image's generator writes to
  **stdout**; the host shim creates `.env`: POSIX `umask 077`, Windows an ACL for the current user only (`icacls`, asserted in
  Pester). **Refuses to write when state holds `.env` or the `pgdata` volume exists.** All five secrets filled, a canary test greps
  the job log for the generated values, and the **recovery-kit step** shows the key ring once with a confirmation. `make keys` and
  `dev.ps1 keys` call the same command (parity test).
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

- **IN-T1 Tag control and release guard (S2)** — S — Harbor/sonnet — Bulwark — none — a tag ruleset limits `v*` creation to Keel's
  identity (Q-N5); `release.yml` refuses unless the tagged commit is reachable from `main` and its PR head had a green
  `ci-required`; signing runs in a protected environment with a required reviewer. Test: a tag on an unmerged branch commit fails the
  `verify` job.
- **IN-T2 Signed `CHANNEL` and minimum version (S3)** — S — Harbor/sonnet — Bulwark — IN-T1 — `release.yml` signs `SHA256SUMS` and a
  one-line `CHANNEL` with Sigstore; the stub embeds a minimum version. Test: a stale but validly signed old release is refused by
  a fresh install (freeze test); a `latest.json` that names a version `CHANNEL` does not is refused.

### E-I2 Endpoints on our domain (Cloudflare, plan only until the owner says go)

- **IN-E0 Read-only inventory** — done (2026-11-22): `cf` signed in; one assets-only Holdfast Worker exists (`cloud.` preview); R2 not
  enabled (`[10042]`); no Pages projects; the account also holds unrelated Workers; the apex returns 404 for `/install.sh` and
  `/install.ps1`. No change made. `cf` is the default tool; `npx wrangler` works on the same account and is used only if a Wrangler
  config is ever committed.
- **IN-E1 `get.holdfastfinance.app` Worker** — S — Harbor/sonnet; deploy by Keel with `cf` — Bulwark — IN7, Q6, Q7 — `infra/get-site/`
  holds the stubs, `_headers`, `_redirects`, `latest.json`. After deploy: `curl -sI` shows `text/plain`, `nosniff`, `no-cache`; `/latest`
  302s to the GitHub Release; `workers.dev` and preview URLs are off; the Worker has no script and no bindings; a CAA record exists
  for the CA in use; the deployed `install.sh` hash equals the Release asset's (test fails otherwise). **The install page lists the
  GitHub-URL one-liner and the stub hash from the Release notes.**
- **IN-E2 Deploy step in RELEASES.md** — S — Beacon + Harbor — Keel — IN-E1 — the release checklist has the `cf` command and the
  hash-comparison step.

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

- **IN10 `install.ps1` stub and core** — M — Harbor/sonnet — Bulwark, Gadfly — IN1, IN-A2, IN-T2, IN5, IN6, Q4 — Pester: tampered hash 20,
  bad bundle 21, downgrade 22; `-DryRun`; PSScriptAnalyzer clean; **under 80 lines**; a test proves it only gets `pwsh` and hands off
  (reads no secret, writes only to a temp directory, runs no repo content before verification). **A CI job runs the bootstrap under
  `powershell.exe` 5.1 on `windows-2025`**, forces TLS 1.2, and takes the hash-pinned PowerShell MSI path when `winget` is absent
  (simulated). A **preflight** reports build, edition, firmware virtualization, RAM, disk, winget, WSL and `pwsh` *before any download*.
  Pester covers the paranoid path including `Unblock-File`. If Q4 is refused, the stub is the two-step form with the same acceptance.
- **IN11 Inno Setup `.exe`** — M — Harbor/sonnet — Bulwark — IN10, IN-O1 — `windows-2025` builds it (Inno Setup is preinstalled), installs
  silently to a temp per-user dir, checks files, the Start-menu shortcut and the Apps entry, uninstalls, and asserts the code directory
  is gone, **the state directory is untouched**, and nothing under `%ProgramFiles%` was written. `/VERYSILENT` never installs Docker.
  The build fails if `[UninstallDelete]` names any state path.
- **IN-W-S1 Spike: WSL 2 on a hosted runner** — S — Harbor/sonnet — none — IN1 — **assume the answer is no** and plan the owner-hardware
  path already (IN-S0, IN-W3): hosted Windows runners do not run WSL 2 or Linux containers in practice (Gadfly S8). The spike runs once
  to record the evidence; if it unexpectedly passes, a hosted stack test is added.
- **IN-W1 Reboot-resume and WSL path** — M — Harbor/sonnet — none — IN11 — on the owner's personal PC (or a clean Windows VM): no WSL, no Docker
  → the installer elevates once, reboots, resumes, reaches a healthy stack. Evidence: a log of steps and exit codes.
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
- **IN13 Homebrew tap formula** — S — Harbor/sonnet — Bulwark — IN12, Q11 — `brew install --build-from-source` and `brew test` pass from our
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
- **IN-M4 `.dmg` wrapper** — S — Harbor/sonnet — none — IN14 — only on request.
- **IN-M5 Notarized `.pkg`** — M — Harbor/sonnet — Bulwark — IN14, Apple enrolment — **deferred by owner decision.** Listed so the work is not lost.

### E-I6 Update, uninstall, phone access, docs

- **IN15 `update`, `restore`, `uninstall` safety** — L split into M + M — Harbor + Tide/sonnet — Bulwark, Ledger (money data) — IN-A2, IN-B1, IN7, IN-T2 —
  (a) update from N to N+1 on a synthetic household: backup first, **verified by a scratch restore**, refuses to continue if the backup
  fails (test breaks the backup and sees the refusal); stops `api` and `worker` before migrating; keeps N-1 bundle and digests;
  (b) **a migration fails halfway, `holdfast restore` runs on N-1, and the data is byte-identical** (hash of dump before and after);
  (c) Postgres 16 to 17 goes through dump and restore with a test; (d) downgrade and below-minimum refused; (e) **uninstall keeps state
  and volumes; install, create data, uninstall, reinstall, and an old encrypted row still decrypts and the old Postgres password still
  works**; `--purge` needs the typed household name; neither touches Docker or Tailscale.
- **IN16 Phone access per platform** — S — Harbor/sonnet — Bulwark — IN-A2, IN-W3, IN-M3 — the sidecar overlay starts on Linux and Docker
  Desktop (Windows now, macOS when available) with ADR 0021's serve-config test; `holdfast phone off` returns to loopback only.
- **IN17 Install docs, release notes, support** — S — Beacon/sonnet — Harbor — IN7 — `docs/setup/install.md` per OS: the one-liners (both
  origins), the paranoid path, verify commands, the "unsigned preview" banner, where data lives, how to back up, uninstall. Issue templates
  ask for a log with a redaction warning (N4). A link-check job passes and a test asserts every command in the page appears in a CI job.

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

1. **IN-S0** the owner-hardware spike on the Windows PC. First, cheap, and it tells us whether the stack even runs on Docker Desktop.
2. **IN0, IN1**, then in parallel **IN-A1** (local ingress), **IN-B1** (state and backup), **IN3**, **IN5**, then **IN2**, **IN4**, **IN6**.
   After this a Windows user can reach a usable app and cannot lose data silently.
3. **IN-A2** (wrapper) and **IN-O1** (launcher), then **IN-T1 and IN-T2** (tag control, `CHANNEL`).
4. **IN10, IN11, IN-W1, IN-W2** (Windows stub, `.exe`, reboot path, SignPath), then **IN-W3** acceptance on the personal PC.
5. **IN7, IN8, IN9** (Linux script, distros, acceptance). Cheap, so it may run in parallel with step 4 when a window is free.
6. **IN12, IN13, IN14** (macOS script, tap, unsigned `.pkg`), CI-only.
7. **IN15** (backup-first update, restore, reinstall-decrypts), then **IN-E1/E2** (endpoints), then **IN17** (docs). **No installer channel is published
   before IN15 passes (N1 default).**
8. After that: IN16, IN-W4, IN-L-S1, IN-L-S2, IN-W-S1, IN-M-S1 (spikes and extras), **IN-M3** when a Mac exists, IN-M4 and IN-M5 on request.

## Owner questions

Answered 2026-11-22: macOS unsigned `.pkg` plus Homebrew and terminal first, no Apple enrolment; Windows signing via SignPath; test
hardware is a personal Windows PC and no Mac. Remaining, each with cost and what it unlocks:

| # | Question | Cost | Unlocks |
|---|---|---|---|
| Q3b | A **Mac for Docker Desktop and Safari testing**: borrow one, or rent an Apple-silicon host for a few acceptance sessions? (No macOS VM on non-Apple hardware: Apple's licence.) | A rental, price UNVERIFIED (hourly or monthly exist; get quotes). A borrowed Mac: $0 | `IN-M3`, ADR 0021 U-7, the macOS phone-access check; before it, macOS is "script- and package-tested, stack unverified". |
| Q4 | Amend **hard rule 10** to permit exactly one 5.1-compatible bootstrap file, `installer/install.ps1` (at most 80 lines, test-enforced to only fetch `pwsh` and hand off)? Keel rules on CLAUDE.md. | $0 | A true one-line Windows command on a stock PC; `IN10`. Otherwise two lines. |
| Q5 | `.pkg` only, or also a `.dmg` wrapper? | $0, one small slice | `IN-M4`. |
| Q6 | Authorise Keel to create the `holdfast-get` assets-only Worker and the `get.` custom domain with `cf`, and decide how releases deploy: by hand, or a scoped Cloudflare token as a protected-environment secret? | $0 on the free plan (static assets free and unlimited) | `IN-E1`, `IN-E2`. |
| Q7 | Move Holdfast to its **own Cloudflare account** (this one also holds unrelated projects), with hardware-key 2FA, a **CAA record pinned to the CA in use**, and DNSSEC kept on? Cloudflare is the code-execution root for every `get.` one-liner (ADR 0037 §4). | $0 (registrar at cost unchanged); a zone move has a short DNS-gap risk | Smaller blast radius for the one place a swapped stub could come from. The GitHub-URL one-liner needs no answer. |
| Q9 | Docker Desktop is free for personal and small-business use and paid for larger organisations. Default to it on Windows and macOS with Colima and Docker-in-WSL as the free routes, or make the free routes the default? | $0 | Wording and defaults in `IN10`, `IN12`, docs. |
| Q10 | Priority of **Podman** (the owner runs Fedora with podman sockets) and **Raspberry Pi** (arm64): now or after Windows and macOS? | arm64 build minutes are free on a public repo | Order of `IN-L-S1`, `IN4`, `IN8`'s distro tail. Default: after. |
| Q11 | Create `holdfast-finance/homebrew-tap` and a winget publisher identity under the org? | $0 | `IN13`, `IN-W4`. |
| **N1** | Do we publish any one-liner, `.exe`, `.pkg` or tap **before an upgrade path exists** (beta)? Default in ADR 0037: **no**; the first channel release is the first with a tested backup-first `update` and `restore`. | $0; the cost is a later first release | Whether strangers can install a version they cannot upgrade from. |
| **N2** | **Key-ring escrow:** where does a non-technical user keep `FERNET_KEY`, and do off-host backups include it? Including it defeats encryption at rest for the backup; excluding it makes the backup useless without the kit. Default: exclude it from off-host backups, show a one-time recovery kit, keep a protected copy beside `.env` for local rollback. | $0 | `IN3`, `IN-B1`, `IN15`. |
| **N3** | **Who is the self-host `.exe` for?** BRAND frames self-host as builder-to-builder. Should a non-technical person who has never heard of Docker be pointed at Holdfast Cloud instead of a Docker Desktop install? | $0 | Installer copy, how much hand-holding the preflight and launcher carry, and docs tone. |
| **N4** | **Support channel:** failed installs will arrive as public issues with logs. Approve issue templates with a redaction warning, and say who triages before launch? | $0 | `IN17`. |
| **N5** | **Who may create `v*` tags?** Proposal: Keel's identity only, enforced by a tag ruleset, with `release.yml` refusing a commit not on `main`. | $0 | `IN-T1`, the trust in every signature. |

Removed: the old Q1 (Apple enrolment: deferred by owner decision, `IN-M5` waits), Q2 (answered: SignPath), Q3 (answered, and its self-hosted-runner
option is forbidden), and Q8 (public-repo timing: moot, the repo is public).
