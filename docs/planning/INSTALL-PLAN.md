# Install-everywhere plan

Owner: **Harbor** (drafts), **Keel** (schedules). Decision record: `docs/adr/0037-install-on-every-platform.md`
(Proposed). Nothing here is built. Dates are not promised: sizes are S = 1 run, M = 2 to 3 runs, L = split
before scheduling (ROADMAP units). Prefix `IN`. Each story gets a brief under `docs/planning/briefs/<ID>.md` when scheduled.

## Where this sits

- **Preemptible lane.** The Q1 finish line is Phase 5 (`ROADMAP.md`). This work runs only when the critical path
  is blocked, with one exception: **IN0 and IN1 are cheap and unblock everything**, so Keel may pull them forward.
- **Hard dependency on ADR 0021.** A stranger-grade first run needs the local ingress (`127.0.0.1:47800`, plain
  HTTP, no certificate warning) and the `holdfast` wrapper (`up`, `update`, `backup`, `recover`). Those are PA1
  and PA-D* in the ADR 0021 plan. This plan does not rebuild them. Where a slice needs them it says `PA1`, and
  until then the slice delivers a degraded path (self-signed `https://localhost`, which Chrome blocks for
  WebAuthn) that is labelled "preview".
- **The repo is private until PLAN 17.6.** Signed installers, free arm64 runners, SignPath and a public Homebrew
  tap all wait for it. The unsigned, script-only paths do not.

## Facts that shape the order (all from the ADR, checked in the repo 2026-11-22)

- There is no Ubuntu-specific code. The real gaps: install means "clone + make + dev stack", images are
  `linux/amd64` only, nothing but `ubuntu-latest` has ever run, Windows has never run (PHASE-0 blocked item).
- `ubuntu-24.04` hosted runners ship Docker 28.0.4, **below the 28.3.3 floor**. `ubuntu-26.04` ships 29.4.2 and
  Podman 5.7.0. Windows 2025 runners ship Docker 29.7.2 but for Windows containers. macOS runners ship no Docker.

## Stories

Format: `ID title — size — owner/model — reviewers — depends — acceptance (evidence that can fail)`.

### E-I0 Remove the Ubuntu-only assumptions (do first)

- **IN0 Portability audit as a test** — S — Harbor/sonnet — Bulwark (compose is protected) — none —
  `scripts/check-portability.py` fails CI on: a `bash` shebang in a script the installer ships, `sha256sum` or
  `sed -i` without a BSD fallback in shipped scripts, a host-path bind mount in the release compose, a `build:`
  key in the release compose, a published port other than the allowed loopback one. Seed each violation once in a
  test and see it fail.
- **IN1 Release bundle and release compose** — M — Harbor/sonnet; contract by Lattice — Bulwark, Lattice — IN0 —
  `make bundle` produces `holdfast-<ver>-bundle.tar.gz` and `.zip` with `compose.yaml` pinned `@sha256`, no `build:`,
  no dev override. A CI job runs `docker compose config` on it, `scripts/check-compose-ports.py` passes, and
  `docker compose up -d --wait` on a clean runner reaches `/api/health` with the dev files absent (delete
  `docker-compose.dev.yml` and `./backend` first, so a leftover mount fails the job).
- **IN2 Named documents volume (no host chown)** — S — Harbor + Tide/sonnet — Bulwark — IN1 — the stack starts
  and stores and reads a synthetic document with no `chown` helper and no bind mount, as uid 10001, on a fresh
  volume. Restart keeps the file. (Fallback if Bulwark objects: bind mount with `:z` and `:U`; spike L-S2.)
- **IN3 `holdfast keys` inside the api image** — S — Harbor/sonnet — Bulwark — IN1 — `.env` produced with mode 0600,
  all five secrets filled, no value printed (a canary test greps the job log for the generated values), refuses to
  overwrite. Parity: `make keys` and `dev.ps1 keys` call the same command, and a test asserts both do.
- **IN4 Multi-arch images** — M — Harbor/sonnet — Bulwark (protected path `release.yml`) — IN1 — the images job
  builds `linux/amd64,linux/arm64`, `docker buildx imagetools inspect` lists both, and the arm64 image starts the
  stack on `ubuntu-24.04-arm` (public repo only; on a private repo the run is `workflow_dispatch` QEMU, recorded
  minutes). Budget line added to ADR 0013's table.
- **IN5 Docker floor check, shared** — S — Harbor/sonnet — Bulwark — none (ADR 0021 defines it) — one function
  (`scripts/lib/docker-floor.sh`, a PowerShell twin) compares `docker version` numerically. Table-driven tests: 28.0.4
  refused, 28.3.3 accepted, 29.9.3 accepted, `24.0.7`, `20.10.24`, garbage and empty refused with a named message.
  Both languages run the same JSON table, and a test fails if the tables diverge.
- **IN6 Windows host without `make` or Python** — S — Harbor/sonnet — none — IN3 — `dev.ps1 keys/up/down/ports` no
  longer need `python3`/`python` on the host (the `ports` check moves into a container or a PowerShell port). Parsed
  on `windows-2025` with PSScriptAnalyzer, and run in a `pwsh` container for `help`.

### E-I1 Endpoints on our domain (Cloudflare, plan only until the owner says go)

- **IN-E0 Read-only inventory** — done in the ADR (2026-11-22): `cf` is signed in, one assets-only Holdfast Worker
  exists (`cloud.` preview), R2 is not enabled (`[10042]`), no Pages projects, and the account also holds unrelated
  Workers from other projects. No change was made.
- **IN-E1 `get.holdfastfinance.app` Worker** — S — Harbor/sonnet; deploy by Keel with `cf` — Bulwark — IN7 and owner
  questions Q6, Q7 — `infra/get-site/` holds the stubs, `_headers`, `_redirects`, `latest.json`. After deploy: `curl -sI`
  shows `text/plain`, `nosniff`, `no-cache`, HTTP/2 over TLS 1.2+; `/latest` returns 302 to the GitHub Release; a
  `workers.dev` and preview URL are off; the Worker has no bindings and no script (assets only). A test fails if the
  deployed `install.sh` hash differs from the Release asset's.
- **IN-E2 Deploy step in RELEASES.md** — S — Beacon/haiku + Harbor — Keel — IN-E1 — the release checklist has the
  `cf` deploy command and the "compare the hash printed on the site" step. Optional later: a release-workflow job with
  a scoped token behind a protected environment (needs Q6's answer).

### E-I2 Linux and other Unix script

- **IN7 `install.sh` stub and core** — M — Harbor/sonnet — Bulwark (every download path), Gadfly (challenge) — IN1, IN3,
  IN5 — shellcheck clean under `sh` and `dash`; `--dry-run` prints the plan without side effects; on a mismatched
  SHA256 the job fails with exit code 20 and leaves nothing on disk (planted tampered bundle in the test); on a bad
  Sigstore bundle exit 21; on a downgrade exit 22; no `--insecure` flag exists (a test greps for it); the stub is
  under 80 lines (a test counts).
- **IN8 Distro detection and `--install-docker`** — M — Harbor/sonnet — Bulwark — IN7 — table-driven tests of
  `/etc/os-release` fixtures for Ubuntu 22.04/24.04/26.04, Debian 12/13, Fedora 43/44, Rocky 9, Raspberry Pi OS, Arch,
  Alpine, openSUSE: right action or a named "unsupported, here is the manual page". Real run on `ubuntu-24.04` (Docker
  28.0.4): refuses below the floor, then `--install-docker` upgrades via Docker's apt repo and reaches the floor.
- **IN9 Linux acceptance on real distros** — M — Sounding/sonnet — Keel — IN7, IN8, owner machine — Fedora 43 (the
  owner's box) and a Debian 13 and an Ubuntu 26.04 VM or machine each run the one-liner to a healthy `/api/health`
  and create a household and passkey. Counts and pass/fail only in the report, nothing from the owner's data.
- **IN-L-S1 Spike: Podman** — S — Harbor/sonnet — Bulwark — IN1 — on `ubuntu-26.04` (Podman 5.7.0) and Fedora 43,
  `docker compose` with `DOCKER_HOST` at `podman.sock` (rootless): does `up -d --wait` pass? does `!override`/`!reset`
  parse? do the merge tags survive? cap_drop, read-only root, internal networks, `:z` on the three read-only config
  mounts, loopback bind behaviour? Output is a yes/no table in `docs/ops/`. Pass criterion for "Tier 2": the same
  health and port checks as Docker, plus ADR 0021's loopback probe from a second host.
- **IN-L-S2 Spike: bind mount vs named volume on SELinux** — S — Harbor/sonnet — none — IN2 — decides IN2's fallback
  with evidence from Fedora 43 (Docker, no SELinux option reported) and Podman (enforcing).

### E-I3 Windows

- **IN10 `install.ps1` stub and core (pwsh 7)** — M — Harbor/sonnet — Bulwark, Gadfly — IN1, IN3, IN5, IN6, Q4 — Pester tests: tampered hash → exit 20, bad bundle → 21, downgrade → 22; `-DryRun`; PSScriptAnalyzer clean; the stub
  is under 80 lines; a test proves no path skips verification. If the rule-10 amendment is refused the stub is the
  two-step form and its acceptance is the same.
- **IN11 Inno Setup `.exe`** — M — Harbor/sonnet — Bulwark — IN10 — `windows-2025` job builds it (Inno Setup is
  preinstalled there), installs silently to a temp per-user dir, checks files and the Apps and Features key,
  uninstalls, and asserts the directory is gone and nothing under `%ProgramFiles%` was written. `/VERYSILENT` never
  installs Docker (job asserts `docker` was not touched).
- **IN-W-S1 Spike: Docker Engine in WSL 2, and Linux containers on a hosted runner** — S — Harbor/sonnet — none — IN1
  — answers: does `wsl --install -d Debian` plus Docker CE start the stack on `windows-2025` (nested virtualization is
  UNVERIFIED)? Pass: the hosted runner reaches `/api/health` from Windows `localhost`. If not, Windows stack tests move
  to owner or self-hosted hardware (Q3) and the plan says so.
- **IN-W1 Reboot-resume and WSL path** — M — Harbor/sonnet — none — IN11, IN-W-S1 — on a clean Windows 11 VM or owner PC:
  no WSL, no Docker → installer elevates once, reboots, resumes, reaches a healthy stack. Evidence is a screen-free log
  of steps and exit codes. Never run on employer equipment (PLAN Section 0).
- **IN-W2 Authenticode signing** — M — Harbor/sonnet — Bulwark — IN11, repo public, Q2 — the published `.exe` has a
  valid signature (`Get-AuthenticodeSignature` in CI is `Valid`, signer matches the chosen service, timestamped). A
  test fails if the unsigned build is attached to a Release that claims signed.
- **IN-W3 Windows acceptance and loopback probe** — M — Sounding/sonnet — Keel — IN-W1, Q3 — the PHASE-0 blocked item
  closes: `dev.ps1 keys/up/migrate` and the installer both run on a real Windows host with Docker Desktop. ADR 0021's
  "loopback only" check is repeated from a second LAN host against Docker Desktop's published port: **it must not answer.**
- **IN-W4 winget manifest** — S — Harbor/sonnet — none — IN-W2 — `winget validate` passes and a test submission to
  `microsoft/winget-pkgs` is accepted or the rejection reason is recorded.

### E-I4 macOS

- **IN12 macOS path of `install.sh`** — M — Harbor/sonnet — Bulwark — IN7 — runs under `/bin/sh` on `macos-15`
  (arm64, bash 3.2 and BSD tools) with `--dry-run` and in unit tests with fake `docker` shims; uses `shasum -a 256`;
  no GNU-only flag (shellcheck plus a BSD-tools job). Asserts that a `curl`-downloaded script carries no quarantine
  attribute (`xattr`), to settle UNVERIFIED #1.
- **IN13 Homebrew tap formula** — S — Harbor/sonnet — Bulwark — IN12, repo public — `brew install --build-from-source` and
  `brew test` pass on `macos-15` from our tap; the formula pins the bundle URL and SHA256 and `brew audit --strict` is clean.
- **IN-M-S1 Spike: Colima on Intel and Apple Silicon runners** — S — Harbor/sonnet — none — IN1 — can `macos-15-intel` run
  Colima and the stack? Apple Silicon runners probably cannot nest virtualization (UNVERIFIED). Pass: stack healthy on
  one runner type. If neither works, macOS stack tests are owner-hardware only.
- **IN14 `.pkg`** — M — Harbor/sonnet — Bulwark — IN12, Q1 — `pkgbuild`/`productbuild` job; `pkgutil --check-signature`
  shows a Developer ID Installer; `spctl -a -t install` accepts; `xcrun stapler validate` passes; notarization log is
  attached to the release. Without Q1, the job builds an unsigned pkg to prove packaging, and **does not attach it**
  (a test fails if an unsigned pkg is attached).
- **IN-M3 macOS acceptance and loopback probe** — M — Sounding/sonnet — Keel — IN12, IN14, Q3 — Apple Silicon Mac with Docker
  Desktop then Colima: one-liner reaches a healthy stack natively on arm64 (`docker image inspect` shows `arm64`); passkey
  works in Safari (ADR 0021 U-7 check) and Chrome; the loopback probe from a second host does not answer.
- **IN-M4 `.dmg` wrapper** — S — Harbor/sonnet — none — IN14, Q5 — only if the owner wants it.

### E-I5 Updates, uninstall, phone access, docs

- **IN15 `holdfast update` and `uninstall` verification** — M — Harbor + Tide/sonnet — Bulwark — IN7, PA1 — update from
  version N to N+1 on a synthetic household keeps data and fails closed on a bad signature; downgrade refused; uninstall
  keeps volumes, `--purge` needs the typed name and removes them; neither touches Docker or Tailscale.
- **IN16 Phone access per platform** — S — Harbor/sonnet — Bulwark — PA1, IN-W3, IN-M3 — the sidecar overlay in the
  bundle starts on Linux, Docker Desktop (Windows, macOS) with the same serve-config test as ADR 0021, and `holdfast
  phone off` returns to loopback only.
- **IN17 Install docs and release-note wording** — S — Beacon/sonnet — Harbor — IN7 — `docs/setup/install.md` per OS: the
  one-liner, the paranoid path, the verify commands, the unsigned-preview banner, uninstall. A link check job passes
  and a test asserts every command in the page appears in a CI job.

## CI matrix (free tier)

Runner facts read 2026-11-22 (https://docs.github.com/en/actions/reference/runners/github-hosted-runners, runner-images
READMEs, https://docs.github.com/en/billing/reference/actions-minute-multipliers).

| Job | Runner | What it proves | What it cannot prove |
|---|---|---|---|
| `install-linux-new` | `ubuntu-26.04` (Docker 29.4.2, Podman 5.7.0) | Full one-liner against a local bundle: verify, keys, `up`, health, port check, uninstall. Podman spike (`IN-L-S1`) | Distro package managers other than apt; rootless Podman with systemd user sessions |
| `install-linux-floor` | `ubuntu-24.04` (Docker 28.0.4, below floor) | Refusal below the floor, then `--install-docker` reaches the floor | Debian 12/13 behaviour (same family, different repo codename: covered by fixtures only) |
| `install-linux-arm64` | `ubuntu-24.04-arm` (public repo) | arm64 images start, Raspberry-Pi-class architecture | Pi memory limits; 32-bit OS |
| `install-distro-fixtures` | `ubuntu-latest` + `fedora:43`, `debian:13`, `rockylinux:9`, `alpine`, `archlinux` containers | `/etc/os-release` detection, `--dry-run` plans, script syntax under each distro's `sh` | A real daemon on that distro (the container shares the host's) |
| `install-windows-static` | `windows-2025` | Pester, PSScriptAnalyzer, Inno build, silent install and uninstall to a temp dir, signature check | **The stack.** The image has Docker 29.7.2 for Windows containers; Linux containers and nested virtualization are UNVERIFIED (`IN-W-S1`) |
| `install-windows-arm` | `windows-11-arm` | Installer and scripts run on ARM64 Windows | The stack |
| `install-macos-static` | `macos-15` arm64 | `/bin/sh` 3.2 + BSD tools, brew formula, `pkgbuild`, signature structure, no-quarantine assertion | **The stack** and Gatekeeper's real double-click path (needs a GUI session) |
| `install-macos-intel` | `macos-15-intel` | Colima spike (`IN-M-S1`) | Apple Silicon behaviour |
| `notarize` | `macos-15`, tag push only | Notary accepts and staples | Anything for users without Q1's account |

What cannot be tested on hosted runners, and who covers it:
- **Docker Desktop on Windows or macOS** (needs a licence click-through and a hypervisor): owner machines (Q3), recorded as
  counts and pass/fail, never employer hardware.
- **The reboot after enabling WSL 2** and the SmartScreen and Gatekeeper dialogs: manual acceptance (IN-W1, IN-M3).
- **Fedora, Debian, Rocky with their own daemons**: owner VMs in IN9. The owner's Fedora 43 box is the first real check
  (it has Docker 29.9.3, cgroups v2, SELinux enforcing, no Docker SELinux option, podman installed).
- **LAN reachability of published ports** (ADR 0021's floor claim) from a second host: acceptance runs only.

Minute budget (ADR 0013: 2,000 a month on a private repo; target at most 1,500). Per-minute rates from the billing table:
Linux 2-core $0.006, Windows $0.010, macOS $0.062, so one macOS minute costs about ten Linux minutes and one Windows
minute about 1.7 (ratios derived from the rates; quota multipliers UNVERIFIED). Therefore:
- The installer matrix does **not** run on every PR. It runs on a path filter (`scripts/install*`, `infra/get-site/**`,
  `installer/**`, `release.yml`), nightly on `main`, and on release-candidate tags. A docs-only PR still runs only `checks`.
- Linux jobs run on every matching PR. Windows and macOS static jobs run on path-matching PRs and nightly; the Intel macOS
  spike and `notarize` run on demand and on tags.
- Once the repo is public, standard hosted-runner minutes are not metered (ADR 0013 describes the private-plan limits; the
  public-repo rule is UNVERIFIED here, confirm before relying on it).

## Slice order

1. `IN0 → IN1 → IN3, IN5` (parallel) → `IN2`, `IN4`, `IN6`. Unblocks every platform. Ubuntu-only assumptions end here.
2. `IN7 → IN8 → IN9`, with spikes `IN-L-S1`, `IN-L-S2` alongside. The Linux script is the first user-visible result.
3. `IN-E1/E2` once Q6 and Q7 are answered (the endpoint is useful to Linux immediately).
4. `IN12 → IN13`, `IN-M-S1`: macOS command and tap, no Apple account needed.
5. `IN10 → IN11`, `IN-W-S1`: Windows stub and unsigned `.exe`, no certificate needed.
6. `IN-W1/W3`, `IN-M3`: owner-hardware acceptance (Q3).
7. After the repo is public and Q1/Q2 are answered: `IN-W2`, `IN14`, `IN-W4`, `IN-M4`.
8. `IN15`, `IN16`, `IN17` follow PA1.

## Owner questions

Each: what it costs and what it unlocks. None blocks IN0 to IN8.

| # | Question | Cost | Unlocks |
|---|---|---|---|
| Q1 | Enrol in the Apple Developer Program for Holdfast (an individual or organisation account; organisations may also need a D-U-N-S number, UNVERIFIED)? | **$99 a year** | `IN14` signed and notarized `.pkg`, Homebrew cask in the official repo, `IN-M4`. Without it macOS is the one-liner and our own tap only. |
| Q2 | Windows signing route: apply to **SignPath Foundation** (free; needs a public repo, an OSI licence with no commercial dual-licensing, and a released project) or pay for **Azure Artifact Signing** (price UNVERIFIED, about $10 a month by recall; individuals only in the US or Canada, organisations in a longer list; identity validation)? Or ship unsigned preview only? | $0 or a monthly fee | `IN-W2`, `IN-W4`, fewer SmartScreen warnings (reputation still has to build). |
| Q3 | Who supplies a **Windows 11 PC with pwsh 7** and a **Mac** (ideally Apple Silicon) for acceptance, neither employer-owned? A self-hosted runner on the owner's own machines is an option. | $0 if owned. A rented Mac is not recommended (cost). | `IN-W1`, `IN-W3`, `IN-M3`; closes the PHASE-0 blocked item. |
| Q4 | Amend **hard rule 10** to allow a PowerShell 5.1-compatible bootstrap stub (about 60 lines) that only fetches `pwsh`? Keel rules on CLAUDE.md; the owner's view is needed because it is a stated rule. | $0 | A true one-line Windows command on a stock PC (otherwise: "install PowerShell 7, then run"). `IN10`. |
| Q5 | `.pkg` only, or also a `.dmg` wrapper? | $0, one small slice | `IN-M4`. |
| Q6 | Authorise Keel to create the `holdfast-get` assets-only Worker and the `get.holdfastfinance.app` custom domain with `cf`, and how to deploy releases: Keel runs `cf` by hand (no token in GitHub) or a scoped Cloudflare token as a GitHub environment secret? | $0 on the free plan (static assets are free and unlimited, `_redirects` 2,000 static rules) | `IN-E1`, `IN-E2`, the short URLs. |
| Q7 | Move Holdfast to its **own Cloudflare account** (the current one also holds unrelated projects) before the install endpoint goes live? Zone transfer between accounts is a dashboard operation, with a short DNS gap risk. | $0 (registrar at cost is unchanged) | Smaller blast radius for the one place a swapped stub could come from; a hardware-key-only 2FA policy for Holdfast alone. |
| Q8 | **Public-repo timing** (PLAN 17.6): signing services, free arm64 runners, the public tap and attestations all need it. | $0 | `IN4` on a free runner, `IN13`, `IN-W2`, `IN14`. |
| Q9 | Docker Desktop is free for personal and small-business use and paid for larger organisations. Is "Docker Desktop is the default on Windows and macOS, Colima or Docker in WSL as the free alternatives" the right product stance, or should the free routes be the default? | $0 | Wording and defaults in `IN10`, `IN12`, docs. |
| Q10 | Priority of **Podman** (the owner runs Fedora with podman sockets) and **Raspberry Pi** (arm64): Tier 1, Tier 2 or later? | Release minutes for arm64 builds | Order of `IN-L-S1` and `IN4`. |
| Q11 | Create the GitHub repo `holdfast-finance/homebrew-tap` and a winget publisher identity under the org? | $0 | `IN13`, `IN-W4`. |
