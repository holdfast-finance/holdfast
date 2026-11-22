# 0037. Install on every platform: one verified path per OS, short endpoints on our own domain

- Status: **Proposed (revision 2)**
- Date: 2026-11-22
- Deciders: Harbor (DevOps) drafts; Lattice and Bulwark review; Keel decides. The owner asked for it
  (quoted: "right now we only have ubuntu support we need to ensure other distros, a windows exe and a
  pwsh command and macos dmg or package and then terminal command").
- Reviews: Gadfly on revision 1, `docs/phases/reviews/INSTALL-PLAN-gadfly.md` (REWORK-large). The
  "Gadfly response" section at the end answers every item (B1 to B6, S1 to S12, N1 to N5).
- Owner answers received 2026-11-22: (1) macOS ships an **unsigned `.pkg` plus the Homebrew and terminal route
  first**, with no Apple enrolment yet; (2) Windows signing uses **SignPath's free open-source tier**; (3) test
  machines: a personal Windows PC, **no Mac**.
- Read first: ADR 0018 (release artifacts), ADR 0021 (zero-config private access, the Docker floor), ADR 0015
  (key ring), ADR 0013 (free-tier CI), ADR 0009 (Cloudflare Pages note). Numbering: 0036 is PR #70.
- Nothing here is built. `docs/planning/INSTALL-PLAN.md` holds the slices, the CI matrix and the owner questions.

## Revision 2 changes (from Gadfly; all text, no new spike)

- **The repository is public** (`gh repo view` returned `PUBLIC` on 2026-11-22, and `v0.0.0-alpha` was published
  2026-11-21). Every "private until PLAN 17.6" assumption is gone (B1).
- **State is separate from code.** `.env` and the key ring live in a per-user *state directory* that no uninstaller
  or updater removes, and the compose project name is pinned (B2).
- **Updates are backup-first, rollback is restore.** A verified host-side backup is mandatory before every update,
  the one-way changes are listed, and backups are on by default on Windows and macOS (B3, B4).
- **The first usable run is step one**, and it depends on the local ingress (ADR 0021 PA-D2) and the wrapper
  (PA-D6). Both are pulled into this lane (B5).
- **No self-hosted runner on a machine that holds real data** (B6).
- Also: a GitHub-URL one-liner of equal standing, a function-wrapped stub, a freeze-attack guard, tag-creation
  controls, a preflight and a `holdfast open` launcher, a per-OS secrets shim, and a corrected runner table (S1 to S12).

## Context

### What exists today (read from the repo on 2026-11-22)

The honest finding is that **the repo contains no Ubuntu-specific code**: no `apt`, no `systemd`, no distro paths.
"Only Ubuntu" is really four other facts:

1. **The install is the dev stack, from a clone.** README and `docs/releases/v0.0.0-alpha.md` say `git clone`, then
   `make keys && make up && make migrate`. `make up` runs `docker-compose.dev.yml`: hot reload, a `node:24-alpine`
   container running Vite, `./backend` mounted over `/app`. Every base service has a `build:` entry, so the user's
   machine compiles the images. Nothing pulls the GHCR images that `release.yml` publishes. There is no
   production-style compose file for a stranger: `infra/docker-compose.prod.yml` needs `TAILSCALE_BIND_IP`,
   `APP_HOST` and a Cloudflare DNS-01 token (a power-user path).
2. **Host tools the user must already have:** `git`, GNU `make`, `bash`, `python3` (for `scripts/gen-keys.py` and
   `check-compose-ports.py`), `curl`, and for `make test`, `uv` and `pnpm`. `make` does not exist on stock Windows
   or macOS. `dev.ps1` mirrors the Makefile (hard rule 10) but still needs `python3`, which on Windows is often
   the Microsoft Store stub.
3. **The Docker floor (ADR 0021 §2): Engine 28.3.3.** Fedora 43 and Ubuntu 24.04 with `noble-updates` meet it;
   Debian 12 and 13 and Raspberry Pi OS do not (`docker.io` 20.10.24 and 26.1.5, ADR 0021 §7). GitHub's
   `ubuntu-24.04` runner image ships Docker Server **28.0.4**, below the floor, while `ubuntu-26.04` (listed as a
   general-availability label in the runner-images README, no preview badge) ships **29.4.2** and Podman 5.7.0
   (https://raw.githubusercontent.com/actions/runner-images/main/images/ubuntu/Ubuntu2404-Readme.md,
   `.../Ubuntu2604-Readme.md` and `.../README.md`, read 2026-11-22).
4. **It has only ever run on Linux, and on amd64.** CI is `ubuntu-latest` only. `release.yml` builds
   `platforms: linux/amd64` (line 180). PHASE-0's blocked item says `./scripts/dev.ps1 up` has never run on a
   Windows host with Docker Desktop (`docs/phases/PHASE-0.md`).

Not a problem (checked): `.gitattributes` forces `eol=lf` for everything but `*.ps1`, so a Windows checkout does not
hand CRLF scripts to Linux containers.

Other portability hazards found in the compose files (each is a slice or spike in the plan):
- `./data/documents` is a **bind mount** that `make up` pre-chowns to uid 10001 with a root helper container. On
  Docker Desktop (Windows paths, macOS file sharing) ownership semantics differ (UNVERIFIED), and on SELinux hosts
  with Podman a bind mount needs the `:z` label.
- Compose uses the `!override` and `!reset` merge tags. They need Docker Compose v2.24+ and are unlikely to work in
  `podman-compose` (UNVERIFIED), so Podman must go through the real `docker compose` plugin.
- Caddy publishes `127.0.0.1:443`. Rootless Podman cannot bind ports below 1024 by default (UNVERIFIED on 5.x). ADR
  0021 moves the local site to `127.0.0.1:47800` (PA-D2), which removes the problem and the self-signed-certificate
  warning. Chrome disables WebAuthn on certificate errors, so **a usable first run depends on PA-D2.**
- Compose derives the project name from the directory name. Reinstalling to another folder would create *new, empty*
  volumes beside the old ones. The project name must be pinned (§2).
- No `holdfast` wrapper exists yet (ADR 0021 plans one; it is PA-D6, now superseded by this ADR's slices).
- `scripts/gen-keys.py` already warns (line 95) that a new `FERNET_KEY` cannot decrypt data written under the old one
  and that an existing `pgdata` keeps the old role passwords. That sentence is the reason for §2 and §11.

### Constraints

- **Hard rule 12:** no public port. Caddy binds loopback (or the Tailscale IP). Installers never open a firewall port.
- **Hard rule 10:** Windows scripts target PowerShell 7. §5 proposes one narrow, tested exception for Keel to rule on.
- **Hard rule 9:** every new external service is named in §10.
- **Hard rule 3 and ADR 0018 item 5:** no telemetry, no update ping, no secret ever printed.
- **ADR 0013:** no paid plan or licence for CI/CD. Its private-repo arithmetic no longer binds standard hosted
  runners now the repo is public (plan, "CI matrix"), but the principle stands: nothing paid by default.
- **ADR 0005:** AGPL-3.0-only. Anything we bundle or link must be compatible.
- Holdfast is a Docker Compose stack of seven services. An installer is: check or install a container runtime,
  fetch a verified release, generate secrets, start the stack, open the browser, offer phone access (ADR 0021).

## Decision

### 1. One core, thin per-OS shells

All platforms share one **release bundle** and one **`holdfast` command** (the wrapper, which this ADR's slices
build instead of ADR 0021's PA-D6). The OS installers get a runtime in place, put the bundle on disk, and call
`holdfast up`. A bug is fixed once. `make` and `dev.ps1` stay for contributors, in parity.

### 2. The release bundle, the state directory, and backups

Each GitHub Release gains, next to the wheel and sdist:

| Asset | Contents |
|---|---|
| `holdfast-<ver>-bundle.tar.gz` and `.zip` | `compose.yaml` (images pinned `@sha256`, no `build:`, no dev override, no source mounts), `Caddyfile`s, `postgres/init/00-roles.sh`, `.env.example`, `holdfast` (POSIX sh), `holdfast.ps1`, `VERSION` |
| `SHA256SUMS` and `SHA256SUMS.sigstore.json` | One line per asset; a Sigstore bundle signing it (§9) |
| `CHANNEL` and `CHANNEL.sigstore.json` | A signed one-line file: the release's version and the minimum installable version (§8) |
| `install.sh`, `install.ps1` | The bootstrap stubs, also attached so they can be verified |
| platform installers | `holdfast-<ver>-setup.exe`, `holdfast-<ver>.pkg` (§5, §6) |

Images are multi-arch (`linux/amd64,linux/arm64`) from the first installer release. The repo is public, so the free
`ubuntu-24.04-arm` runner is available now.

**Code and state are separate** (Gadfly B2). Code is replaceable; state is the only copy of the household's ability
to read its own data.

| | Code (replaced by update, removed by uninstall) | State (kept by uninstall, removed only by `--purge`) |
|---|---|---|
| Linux | `${XDG_DATA_HOME:-~/.local/share}/holdfast/` (current bundle plus the previous one) | `${XDG_STATE_HOME:-~/.local/state}/holdfast/` |
| macOS | brew Cellar/prefix, or `/usr/local/lib/holdfast` for the `.pkg` | `~/Library/Application Support/Holdfast/` |
| Windows | `%LOCALAPPDATA%\Programs\Holdfast` (the Inno `{app}`) | `%LOCALAPPDATA%\Holdfast` (never under `Programs\`, never in `[UninstallDelete]`) |

State holds: `.env` (the `FERNET_KEY` key ring and the Postgres role passwords), `install.json` (installed version,
previous version), `backups/`, and the recovery-kit receipt. Rules:
- Compose is always run as project `holdfast` with `--env-file <state>/.env`, so a reinstall to another folder finds
  the *same* named volumes (`pgdata`, `redisdata`, `docs`, `caddy_*`).
- `holdfast keys` **refuses to write if state already holds a `.env` or the project's volumes exist** (it detects an
  existing `pgdata` volume by name), and says so. Only `--purge` ends that state.
- State is not placed under the brew Cellar or prefix, and Inno's `[UninstallDelete]` has no state path.
- **Test (plan IN15):** install, create a household and an encrypted row, uninstall, reinstall, and the old row still
  decrypts and the old Postgres passwords still work.

**Secrets generation (S7).** `holdfast keys` runs the generator inside the api image and writes **to stdout**. The host
shim creates the file, because a container writing through a bind mount creates it as uid 10001 and "mode 0600" means
nothing on NTFS. POSIX: `umask 077` then redirect. Windows: create the file, then set an ACL for the current user only
with `icacls`, and assert it in Pester. The shim never prints a value. After writing, it runs the **recovery-kit step**
(§11): the key ring is shown once for the user to store, with a confirmation that they did.

**Documents** become a named volume initialised by a one-off container, so no host `chown`, `:z` label or NTFS
ownership assumption is needed. Cost: a document is read on the host with `holdfast export` or `docker cp`. (Fallback:
keep the bind mount with `:z`/`:U`; spike IN-L-S2.)

**Backups are on by default on Windows and macOS** (Gadfly B4). Docker Desktop and Colima keep named volumes in a VM
disk the user does not know exists. Docker Desktop "Clean / Purge data", "Reset to factory defaults", uninstalling
Docker Desktop, `wsl --unregister docker-desktop` and `colima delete` each delete every volume, with no Holdfast code
involved (Gadfly's list; the exact menu names are UNVERIFIED here and are checked in IN-S0). So:
- A scheduled `holdfast backup` writes a dump plus the documents to the **host filesystem** (`<state>/backups/` by
  default, a user-chosen folder recommended) from first run and **before the first import**. Windows uses a Task Scheduler
  task, macOS a launchd agent, Linux a systemd user timer (offered, on by default only if the user agrees).
- A test asserts the backup directory is a host path and not a volume.
- The first-run page and `holdfast uninstall` state where the data physically lives and how to get it out.
- Off-host copies (restic, Phase 9) layer on top later. The local backup and restore are part of this plan (IN-B1)
  because without them the installer endangers data.

### 3. Runtime per platform

| Platform | Default runtime | Why | Alternatives (tier) |
|---|---|---|---|
| Linux | **Docker Engine ≥ 28.3.3** (Docker CE from Docker's repo, or a distro package that meets the floor) | The stack and ADR 0021's floor were built and verified against Docker. | Rootless/rootful Podman 5 with the real `docker compose` plugin on `podman.sock`: **Tier 2, behind spike IN-L-S1.** `podman-compose`: not supported (merge tags). |
| Windows | **Docker Desktop, WSL 2 backend** | The only first-party GUI runtime. Per-user install needs no admin. | Docker CE inside a WSL 2 distro (no Desktop licence): Tier 2, spike IN-W-S1. Podman Desktop, Rancher Desktop: Tier 3. |
| macOS | **Docker Desktop** | Same reason; ADR 0021's counts assume it. | **Colima** (`brew install colima docker docker-compose`): Tier 2, the scripted no-licence path. OrbStack: allowed, never scripted (personal use free, $8 per user per month for business, https://orbstack.dev/pricing, read 2026-11-22). Rancher Desktop: Tier 3. |

Tier 1 means CI or an owner machine proves it before release. Tier 2 means a documented path with a spike behind it.
Tier 3 means "may work, not tested".

Facts (all read 2026-11-22 unless marked):
- **Docker Desktop licence.** Free for personal use, small businesses (fewer than 250 employees and under $10M annual
  revenue), education and non-commercial open source. Paid for professional use in larger organisations and government
  (https://docs.docker.com/subscription/desktop-license/). The installer **never installs Docker Desktop silently**. It
  prints that sentence, links the licence, and installs only on an explicit yes. Anyone on the paid side is pointed at
  Colima or Docker CE in WSL 2.
- **Docker Desktop floors.** Windows: 64-bit CPU with SLAT, **8 GB RAM, hardware virtualization enabled in BIOS/UEFI**,
  WSL 2 backend per-user without admin (https://docs.docker.com/desktop/setup/install/windows-install/). macOS: the
  newest macOS and the previous two, **at least 4 GB RAM**, Rosetta 2 recommended
  (https://docs.docker.com/desktop/setup/install/mac-install/). The stack's own RAM and disk cost is **to be measured**
  (IN-S0) and printed by the preflight.
- **WSL 2:** `wsl --install` needs an elevated prompt, Windows 10 build 19041 or later, and a **reboot**
  (https://learn.microsoft.com/en-us/windows/wsl/install). The installer survives the reboot (§5).
- **First launch of Docker Desktop needs a GUI terms click**, so `winget install` followed by `holdfast up` fails on a
  machine where nobody has opened it (Gadfly S5). `holdfast open` (§5, §6) detects "engine not ready", opens Docker
  Desktop, waits with a printed explanation, and continues. The step counts in ADR 0021 §7 gain one step for this.
- **Docker Engine on Linux** supports Fedora 43/44/45, Ubuntu 22.04/24.04/26.04/26.10, Debian, RHEL, CentOS, Rocky,
  AlmaLinux and Raspberry Pi OS on amd64 and arm64 (https://docs.docker.com/engine/install/). `get.docker.com` is "only
  recommended for testing and development environments" (https://docs.docker.com/engine/install/ubuntu/), so we do
  not use it: `install.sh --install-docker` follows the documented repository steps with a printed plan and a prompt.
- **The `docker` group is root-equivalent** (https://docs.docker.com/engine/install/linux-postinstall/). The installer
  says so before adding the user.
- **Podman:** `podman compose` wraps an external provider such as docker-compose
  (https://docs.podman.io/en/latest/markdown/podman-compose.1.html); Podman Desktop supports Compose
  (https://podman-desktop.io/docs/compose). Licences for Podman (Apache-2.0) and Colima (MIT) are from memory: UNVERIFIED.
- **SELinux.** Docker's bind-mount docs list the `z` and `Z` options (https://docs.docker.com/engine/storage/bind-mounts/).
  On the owner's Fedora 43 box (`getenforce` = Enforcing) the Docker daemon reports no `selinux` security option, so
  Docker does not label mounts. Podman does, so the Podman tier needs `:z` on read-only config mounts (never on
  `$HOME`) or the named-volume change in §2. cgroups v2 is in use (`cgroup2fs`); cgroups v1 is out of scope.
- **The Docker floor applies on every OS** (ADR 0021). Docker Desktop 4.100.0 bundles 29.9.3 (ADR 0021). A Colima VM's
  engine version must be checked (UNVERIFIED). The installer refuses `up` below the floor.

### 4. Short endpoints on our own domain, artifacts stay on GitHub

```
https://get.holdfastfinance.app/install.sh     POSIX sh stub (Linux, macOS)
https://get.holdfastfinance.app/install.ps1    pwsh stub (Windows)
https://get.holdfastfinance.app/latest.json    {"version","prerelease","bundle","sha256sums","sigstore"}, URLs into GitHub
https://get.holdfastfinance.app/latest         302 to the newest Release page on GitHub
https://get.holdfastfinance.app/win            302 to the newest setup.exe asset
https://get.holdfastfinance.app/mac            302 to the newest .pkg asset
```

**A Cloudflare-free one-liner of equal standing (S1).** The same stubs are release assets, so these work with no
Cloudflare in the path:

```
curl -fsSL --proto '=https' --tlsv1.2 https://github.com/holdfast-finance/holdfast/releases/latest/download/install.sh | sh
irm https://github.com/holdfast-finance/holdfast/releases/latest/download/install.ps1 | iex
```

GitHub's `releases/latest` skips pre-releases, so during the alpha the docs use the explicit tag URL
(`.../releases/download/v0.0.0-alpha/install.sh`). The install page lists **both** forms and prints each stub's SHA-256
**on github.com** (the Release notes), not on our domain.

**Said plainly (S1):** Cloudflare is registrar, DNS and the Worker host for `holdfastfinance.app`, and the apex site is
GitHub Pages behind that DNS. **Whoever controls the Cloudflare account is the code-execution root for every
`get.holdfastfinance.app` one-liner**, and can also repoint the apex and obtain a certificate for it. A hash printed on
the apex is therefore not independent. That is why the GitHub-URL form exists, why the stub hash is published in the
Release, and why the Cloudflare account hardening (hardware-key 2FA, scoped tokens, DNSSEC already on per the ops doc,
a **CAA record pinned to the CA in use**, and a separate account, owner question Q7) matter more than any code here.

**Cloudflare mechanism: an assets-only Worker with a custom domain** (`holdfast-get`, route `get.holdfastfinance.app`),
the pattern `docs/ops/domain-email-pages.md` records for `cloud.holdfastfinance.app`:
- Static-asset requests are free and unlimited on Workers. The 100,000 requests a day cap applies to Worker script
  invocations, and an assets-only Worker has no script (https://developers.cloudflare.com/workers/platform/pricing/
  and `/limits/`).
- `_redirects` allows 2,000 static and 100 dynamic rules (https://developers.cloudflare.com/workers/static-assets/redirects/).
  `_headers` sets `text/plain`, `nosniff` and `no-cache` on the stubs.
- Cost on the free plan: $0. (Whether the free plan asks for a card is UNVERIFIED.)
- `.app` is HSTS-preloaded (ops doc); `curl` still gets `--proto '=https' --tlsv1.2`.

Not chosen:
- **R2.** Not needed. It is also **not enabled on the account**: `cf r2 buckets list` returned `[10042] Please enable R2
  through the Cloudflare Dashboard` on 2026-11-22. Free tier is 10 GB-month, 1M Class A and 10M Class B operations, free
  egress (https://developers.cloudflare.com/r2/pricing/).
- **Cloudflare Pages.** A second deploy tool for the same job.
- **GitHub Pages `get.`.** A Pages site has one custom domain, and there are no `_headers`. **S11 resolved:** I checked
  `https://holdfastfinance.app/install.sh` and `/install.ps1` on 2026-11-22: both return **404**, nothing is deployed
  there, and revision 1's phrase "already deployed" was wrong. No inventory or takedown is needed. A fallback copy on the
  apex stays a plan option, not a fact.
- **`raw.githubusercontent.com`.** Tracks a moving branch.

**The endpoint is not a trust root for the artifacts.** `latest.json` and the redirects only point at GitHub Release
assets; every byte that runs is verified (§8). But the **stub** comes from whichever origin the user chose.

**Deploying it.** `cf` is the default tool (the repo has no Wrangler configuration, so the owner's rule picks `cf`);
`npx wrangler` works on the same account and is allowed if a Wrangler config is ever added, at which point the owner's
rule switches to it. v1: Keel deploys after cutting a release, from `infra/get-site/`, recorded in RELEASES.md, with no
Cloudflare token in GitHub. A scoped token in a protected environment is owner question Q6. **This ADR creates no
Cloudflare resource.**

### 5. Windows

- **Format: Inno Setup `.exe`.** Inno Setup's licence (https://raw.githubusercontent.com/jrsoftware/issrc/main/license.txt,
  read 2026-11-22) says: "Permission is granted to anyone to use this software for any purpose, including commercial
  applications, and to alter and redistribute it, provided that the following conditions are met", and the conditions
  are about keeping copyright notices and not misrepresenting origin. The download page *requests* that commercial users
  buy a licence (https://jrsoftware.org/isinfo.php). So the licence text allows our use, including the Holdfast Cloud
  revenue line, and the purchase is a request, not a condition. Residual risk, recorded under ADR 0013: if the project
  ever wants to honour the request, it is a one-off purchase (price UNVERIFIED). WiX needs an "Open Source Maintenance
  Fee" (https://raw.githubusercontent.com/wixtoolset/wix/main/README.md), which fails ADR 0013. MSIX needs a trusted
  certificate and its sandbox fits Docker poorly. Inno Setup is preinstalled on `windows-2025`.
- **Preflight before any download (S5).** The installer and `install.ps1` check, and print the result before doing
  anything: Windows build and edition (10 build 19041+ or 11), 64-bit, **firmware virtualization enabled**
  (`Win32_Processor.VirtualizationFirmwareEnabled`), RAM (Docker Desktop's 8 GB), free disk (measured in IN-S0), winget
  present, WSL state, and PowerShell 7. If virtualization is off it explains the BIOS/UEFI setting in plain words
  instead of letting WSL fail with a hex code.
- **What the .exe does:** per-user install into `%LOCALAPPDATA%\Programs\Holdfast` (no admin), state in
  `%LOCALAPPDATA%\Holdfast`, then the runtime step. If WSL 2 is missing it elevates once and registers a
  resume-after-reboot step. It creates a **Start-menu "Holdfast" shortcut** that runs `holdfast open`: start Docker
  Desktop if it is stopped, wait for the engine, `up`, open the browser. (After a reboot Docker Desktop is often not
  running, so a bookmarked `localhost:47800` just refuses to connect; the shortcut and `holdfast open` fix that.)
  `/VERYSILENT` never installs Docker and exits with a code naming what is missing.
- **PowerShell 7 and hard rule 10.** Stock Windows has only 5.1. **Proposal for Keel**, scoped as tightly as the
  rule allows: amend rule 10 to permit **one** bootstrap file, `installer/install.ps1`, written for 5.1 and 7, with a CI
  test that it does nothing but get `pwsh` and hand off. Requirements (S6):
  - it forces TLS 1.2 (`[Net.ServicePointManager]::SecurityProtocol`);
  - if `winget` is missing it falls back to the PowerShell MSI from the PowerShell GitHub release, **hash-pinned** in
    the stub;
  - a CI job runs it under `powershell.exe` 5.1 on `windows-2025` (IN10);
  - the size limit is **80 lines** everywhere (revision 1 said 60 here and 80 in the plan);
  - a test proves it reads no secret, writes nothing outside a temp directory, and runs no repo content before verification.
  Without the amendment the Windows one-liner is two lines (install pwsh, then run).
- **One-line command (pwsh 7):** `irm https://get.holdfastfinance.app/install.ps1 | iex`, or the GitHub-URL form in §4.
  The stub body is a function called on its **last line**, so a truncated stream runs nothing (S1). `irm` does not set
  the Mark-of-the-Web (widely reported, UNVERIFIED here), so SmartScreen does not gate this path and **our verification
  is the only check**.
- **The paranoid path and Mark-of-the-Web (S6).** A bundle downloaded in a browser carries the Mark-of-the-Web, so
  `RemoteSigned` blocks `holdfast.ps1`. After verifying the hashes, run `Unblock-File` on the extracted folder. The docs
  say so, and a Pester test covers the flow.
- **winget:** `winget install HoldfastFinance.Holdfast` once a manifest is merged into `microsoft/winget-pkgs` (hash
  checked from the manifest; whether moderation accepts an unsigned installer is UNVERIFIED).
- **Signing: SignPath Foundation's free open-source tier (owner decision 2026-11-22).**
  - Conditions (https://signpath.org/terms, read 2026-11-22): an OSI licence **without commercial dual-licensing**, no
    proprietary component, an actively maintained and **released** project, signing only our own source built by our
    own pipeline. The repo is public and `v0.0.0-alpha` exists (2026-11-21), so the "released" condition is plausibly met
    now. **Whether an alpha pre-release counts is SignPath's call**; we ask in the application.
  - On "dual-licensing" (Gadfly S10): Holdfast is AGPL-3.0-only everywhere and Holdfast Cloud is hosting of the same
    code, not a second licence. Gadfly agrees, but the ruling is SignPath's. The application states that **no Cloud-only
    proprietary code ships in any signed artifact** (the installer, `holdfast.ps1`, the bundle's scripts), and CI checks
    that the signed file list matches an allowlist.
  - Signing runs in a protected GitHub environment on tag jobs only (§9). Until SignPath approves, the `.exe` is an
    **unsigned preview**.
  - Azure Artifact Signing (price UNVERIFIED, about $10 a month by my recall; individuals only in the US or Canada) stays
    the paid fallback only if SignPath refuses.
- **Unsigned interim.** The `.exe` ships with a SHA-256 and the signed `SHA256SUMS`. A double-click shows "Windows
  protected your PC": More info, Run anyway. Antivirus and SmartScreen may also flag an unsigned Inno installer that
  downloads and runs scripts (S5); the release notes say so first and recommend the verified `irm | iex` stub.
  Verify with `(Get-FileHash .\holdfast-<ver>-setup.exe -Algorithm SHA256).Hash` and the `cosign verify-blob` line.
- **Uninstall:** Apps and Features runs `holdfast uninstall`, which keeps state (§11).

### 6. macOS

- **Format (owner decision 2026-11-22): an unsigned `.pkg`, plus the Homebrew and terminal routes, first. No Apple
  enrolment yet.** The `.pkg` (built with `pkgbuild` and `productbuild`) installs `holdfast` and a small `Holdfast.app`
  launcher (runs `holdfast open`). A `.dmg` is deferred: it only helps when it carries a drag-to-install app.
- **What an unsigned `.pkg` means for a user.** Gatekeeper blocks the double-click. The user goes to System Settings,
  Privacy & Security, "Open Anyway" (https://support.apple.com/en-us/102445, read 2026-11-22). The old control-click
  "Open" bypass was removed on macOS 15 (UNVERIFIED here), so the docs do not rely on it. Every release says "unsigned
  preview" in its first line and gives the checksum and `cosign verify-blob` commands. **The recommended macOS paths
  are the terminal command and Homebrew**, because neither triggers Gatekeeper.
- **One-line command:** `curl -fsSL --proto '=https' --tlsv1.2 https://get.holdfastfinance.app/install.sh | sh`, or the
  GitHub-URL form (§4). POSIX `sh`, runs under macOS bash 3.2 and BSD tools (`shasum -a 256`, no GNU flags). Files
  fetched with `curl` carry no quarantine attribute (widely known, UNVERIFIED here; a macOS CI job asserts it).
- **Homebrew tap formula:** `brew install holdfast-finance/tap/holdfast` from our own tap. A formula (CLI) avoids
  Gatekeeper and is checksum-pinned by Homebrew. The official homebrew-cask requires artifacts that "must pass Homebrew's
  Gatekeeper checks" (https://docs.brew.sh/Acceptable-Casks), so no official cask until we notarize.
- **Later, if the owner enrols:** Apple Developer Program ($99 a year, https://developer.apple.com/programs/), Developer ID
  certificates and `notarytool`. Those credentials would be a stealable CI secret and go in the protected environment
  (§9, S4). Nothing in this ADR needs them now.
- **Runtime:** Docker Desktop or Colima (§3). The installer checks the floor; on a Mac with no runtime it offers
  `brew install --cask docker` (cask name UNVERIFIED) or Colima, never silently. Apple Silicon needs the arm64 images (§2);
  amd64 images under emulation are too slow for Postgres. The launcher starts a stopped runtime (Colima needs
  `brew services`, S5) before `up`.
- **Testing macOS without a Mac (owner answer 3).** Apple's software licence allows macOS in a virtual machine **only on
  Apple-branded hardware**, so a macOS VM on the owner's Fedora laptop or Windows PC is **not an option**, and neither
  are community macOS-on-QEMU images on non-Apple hardware. (Apple's macOS licence PDF could not be machine-read here, so
  this rests on Keel's statement and Apple's published licence terms: UNVERIFIED by me; confirm in the SLA before anyone
  tries.) Therefore:
  - **GitHub-hosted macOS runners** (free and unlimited on a public repo, 5 concurrent macOS jobs on the Free plan,
    https://docs.github.com/en/actions/concepts/runners/github-hosted-runners and `/reference/limits`, read 2026-11-22)
    test what runs without Docker: script logic with a fake `docker` shim, `pkgbuild` and `productbuild`, the Homebrew
    formula audit, and the no-quarantine check. They have **no Docker** (Keel's finding; the runner image README lists
    none), and Apple Silicon runners cannot nest virtualization (UNVERIFIED), so they cannot run the stack.
  - **The Docker Desktop path** (and ADR 0021's U-7, the Safari `http://localhost` cookie check, which needs Safari)
    waits for a **rented Apple-silicon host or a borrowed Mac** (owner question Q3b, cost UNVERIFIED). Until then macOS is
    "script- and package-tested, stack unverified", and the docs say so.
- **Uninstall:** `holdfast uninstall` (the `.pkg` has no uninstaller), which also runs `pkgutil --forget`. State stays.

### 7. Linux and other Unix: one script, no packages yet

- **`install.sh`** (POSIX `sh`, shellcheck, no bashisms; the body is a function called on the last line): reads
  `/etc/os-release`, finds Docker or Podman, compares the floor, and on `--install-docker` follows Docker's documented
  repository steps for **Debian/Ubuntu (apt)**, **Fedora (dnf)** and **RHEL, CentOS Stream, Rocky and AlmaLinux (dnf)**
  (Raspberry Pi OS 64-bit follows Debian). Anything else (Arch, openSUSE, Alpine, NixOS, NAS firmware) gets a precise
  message and the manual-install link, which is ADR 0021's NAS statement.
- Code under `${XDG_DATA_HOME:-$HOME/.local/share}/holdfast`, state under `${XDG_STATE_HOME:-$HOME/.local/state}/holdfast`
  (§2), `holdfast` linked into `~/.local/bin`. **PATH (S12):** `~/.local/bin` is not on `PATH` on stock macOS (zsh) and on
  some distros. The stub checks `PATH`, says so, and offers to append the line to the shell rc **only with consent**.
  `sudo` is used only for the Docker install and the `docker` group. No systemd unit in v1; Compose restart policies and
  Docker's boot start cover a reboot.
- **No `.deb`, `.rpm`, Flatpak or AUR package from us.** A package only wraps this script, a signed apt or dnf repository
  is a new hosting service and key, and Flatpak cannot run a Docker daemon. An AUR `PKGBUILD` is welcome from the
  community.
- **arm64 and Raspberry Pi:** 64-bit OS, once §2's images exist. Memory floor to be measured (IN-S0).
- **Podman on Fedora:** after spike IN-L-S1, `install.sh` accepts a Podman setup that provides the `docker compose` plugin
  on `podman.sock`. Until then it says "not yet supported".

### 8. How the one-liners avoid "pipe unverified code"

The one-liner is `stub | sh`. The stub runs nothing unverified beyond itself:
1. The stub is small (under 80 lines), pure fetch-verify-exec, with the repo, the Sigstore signing identity, a **minimum
   version** and the SHA-256 of the `cosign` binary per OS and architecture pinned inside it. The whole body is a function
   invoked on the last line, so a truncated download runs nothing.
2. It downloads `SHA256SUMS`, `SHA256SUMS.sigstore.json`, `CHANNEL` and `CHANNEL.sigstore.json` from the **GitHub Release**.
3. It verifies both signatures (`cosign verify-blob --bundle ... --certificate-identity-regexp
   '^https://github.com/holdfast-finance/holdfast/\.github/workflows/release\.yml@refs/tags/v'
   --certificate-oidc-issuer https://token.actions.githubusercontent.com`, flags from
   https://docs.sigstore.dev/cosign/verifying/verify/, read 2026-11-22), then checks the bundle and installers against
   `SHA256SUMS`. A mismatch deletes the download and exits non-zero. There is **no `--insecure` flag** (a test greps for it).
4. **Freeze attack (S3).** `latest.json` is untrusted and could point a first-time install at an old, validly signed,
   vulnerable release. So the stub (a) refuses any version below its embedded **minimum**, raised on every security
   release, and (b) accepts a version only if the signed `CHANNEL` asset names it as the current release for the channel.
   `latest.json` becomes a convenience, never an authority.
5. **Downgrade guard:** an existing install refuses a lower version unless `--version` is given explicitly.
6. **Paranoid path**, first on the install page: download the bundle, `SHA256SUMS` and the bundle files with `gh release
   download` or a browser, run the verify commands by hand, then `./install.sh --local` (Windows: `Unblock-File`, §5).

**Residual risks, said plainly:**
- The stub comes from the origin the user chose. Cloudflare's account is the code-execution root for `get.` (§4); GitHub's
  organisation is the root for the GitHub-URL form.
- The signing identity is "this workflow on a `v*` tag", and **agents can create tags**. §9 adds the controls (S2) and
  states the remainder.
- Privacy: the endpoint sees the requester's IP like any web server. Workers Logs off, no Web Analytics, and the app never
  calls it (ADR 0018 item 5).

### 9. Signing and provenance of the artifacts

- **Images and `SHA256SUMS` are signed keyless with Sigstore from `release.yml`** (ADR 0018 item 6, "signed images before
  the first beta"). Sigstore's public-good service is already named under rule 9 by ADR 0018.
- **Who may create `v*` tags, and what the workflow refuses (S2).** With full autonomy any session with push rights could
  tag a commit that changes `release.yml`, and Fulcio would certify it. So: a **tag ruleset** limits `v*` creation to Keel's
  identity (owner question N5); `release.yml` **refuses** unless the tagged commit is reachable from `main` and has a green
  `ci-required` on its PR head; the signing job runs in a **protected environment** with a required reviewer. Residual
  risk: a compromised Keel identity or GitHub org owner can still mint a validly signed release. That is the root of trust
  we are choosing, and it is the same root as the GHCR images.
- **Windows signing (SignPath) is a second signing path** with its own credential. Credentials for it (and for Apple, if
  ever) live in the same protected environment, used only by tag jobs. This **re-introduces a stealable long-lived secret**,
  which §9's earlier argument against a minisign key warned about: Authenticode requires it, so we accept it, scope it to
  one environment, and rotate it on any suspicion (S4).
- GitHub artifact attestations use the Sigstore public-good instance on public repositories
  (https://docs.github.com/en/actions/concepts/security/artifact-attestations, read 2026-11-22). The plan does not depend
  on them.
- **Why not a minisign or GPG release key:** its private key needs a home that is either an Actions secret or Keel's laptop.
  Keyless ties trust to the workflow. Cost: the stub fetches `cosign` (hash-pinned) and needs Sigstore online.

### 10. External services (hard rule 9)

| Service | Role | New? | Cost |
|---|---|---|---|
| **Cloudflare**: Registrar, DNS for `holdfastfinance.app`, Email Routing, **Workers (static assets) for `get.` and the Cloud preview** | Short install endpoints | Registrar, DNS, Email Routing and the Cloud-preview Worker are **live but not named in any ADR**: the ops doc says Workers are "covered by ADR 0009", which says only "Cloudflare Pages" for the Cloud site, and ADR 0002 is the product-name decision. **This ADR names all of them.** | $0 |
| Cloudflare **R2, KV, D1, Queues, Pages, Workers with code** | not used | Each needs its own ADR line first. R2 is not enabled on the account. | n/a |
| GitHub Releases, GHCR, GitHub Pages, GitHub-hosted runners | Artifacts, images, site, CI | Existing (ADR 0018, ops doc, ADR 0013) | $0 (public repo) |
| Sigstore (Fulcio, Rekor, TUF root) | Keyless signing and verification | Existing (ADR 0018). New at **install time**: the user's machine contacts it | $0 |
| `download.docker.com` (apt and dnf repositories), Docker Desktop installer | Fetched on explicit consent | New, install-time, user-consented | $0 (Desktop licence in §3) |
| Homebrew (`homebrew/core` bottles, our `homebrew-tap` GitHub repo) | macOS channel | New, optional | $0 |
| `winget`, `microsoft/winget-pkgs`, WSL download, PowerShell MSI release | Windows channel | New, optional | $0 |
| **SignPath Foundation** | Windows Authenticode (owner decision) | **New** | $0 (free open-source tier) |
| `cosign` binary releases (`sigstore/cosign` on GitHub) | Verification tool the stub downloads, hash-pinned | New, install-time | $0 |
| Apple Developer Program, notary service | macOS Developer ID | **Deferred** (owner decision: not yet) | $99 a year, when chosen |
| Azure Artifact Signing | Windows signing fallback | Only if SignPath refuses | UNVERIFIED |
| A rented Apple-silicon host (any provider) | Docker Desktop acceptance on macOS | **Not chosen**; would need naming here first | UNVERIFIED |

At run time nothing contacts these except `holdfast update --check`, which is user-initiated (ADR 0018 item 5).

### 11. Update, rollback, uninstall and the recovery kit

**Update is backup-first and fails closed (B3).** `holdfast update`:
1. Runs the Docker-floor check and the signature verification of §8, and refuses a downgrade or a version below the minimum.
2. **Always takes a backup first**: `pg_dump`, the documents, and a copy of the key ring (kept in state with the same mode
   as `.env`), written to a **host** path. **It verifies the backup** by restoring it into a scratch container and checking
   row counts, and **refuses to continue if the backup or the verification fails.** There is no "if a backup is configured".
3. Stops `api` and `worker`, then runs the one-off `migrate` service as the owner role (hard rule 8).
4. Keeps the previous bundle and image digests on disk (N-1).
5. **Rollback is defined as `holdfast restore <pre-update backup>` onto the N-1 digests. It is never an image downgrade.**

**Which changes are one-way** (so rollback must be a restore): Alembic migrations that rewrite data; secret-envelope
rewrites (`hf1` to `hf2`, ADR 0015, PR #73), after which an older image cannot read the rows; and a **Postgres major bump**
(`postgres:16-alpine` is pinned at `docker-compose.yml:91`, and a 16 to 17 image cannot start on an old `pgdata`). Major
bumps are done by dump and restore inside `update`, with a test. Each release's notes list which of these it contains.

**No channel opens before there is an upgrade path (N1).** Default decision: **no installer channel (stub, `.exe`, `.pkg`,
tap) is published while README says "no upgrade path before beta".** The alpha stays on the clone path. The first channel
release is the first one with a tested `update`, `backup` and `restore` (IN15). The owner may override with an explicit
"preview, no upgrade path" banner (owner question N1).

**The recovery kit (N2).** The `FERNET_KEY` ring is shown once at first run for the user to store (password manager,
print), with a confirmation. **Default:** off-host backups **exclude** the key ring (including it would defeat encryption
at rest for the backup), and so are useless without the kit; the *local* pre-update snapshot includes a copy because it
sits beside `.env` with the same protection. Owner question N2 confirms.

**Uninstall (B2):** `holdfast uninstall` stops the stack and removes **code only**. It keeps state (`.env`, key ring,
`install.json`, `backups/`) and the Docker volumes, and prints where they are. `--purge` removes the volumes **and then
state last**, only after typing the household name and showing the location of the last verified backup. It never
uninstalls Docker, WSL, Colima or Tailscale. The Windows Apps entry and macOS `holdfast uninstall` both call this.
Acceptance: reinstall after uninstall decrypts existing data (§2).

### 12. Phone access (ADR 0021) per platform

The sidecar is a **compose overlay inside the same bundle**, identical on every OS: `holdfast phone on` (or the in-app
wizard) starts the stock Tailscale container, userspace mode, no host Tailscale, no TUN, no `NET_ADMIN`. Differences:
- **Linux:** needs Docker ≥ 28.3.3. Under rootless Podman the sidecar needs no extra capability (UNVERIFIED, IN-L-S1).
- **Windows and macOS (Docker Desktop, Colima):** containers run in a VM and the runtime forwards published ports to host
  loopback. ADR 0021's "loopback only" check was done on Linux, so it **must be re-run on Docker Desktop for Windows (the
  owner's PC, IN-S0) and for macOS (when a Mac is available)** before phone access is called supported there.
- **Windows with Docker CE in WSL 2:** `localhost` forwarding is on by default (UNVERIFIED); phone traffic never needs it.
- No installer installs, configures or signs in to Tailscale. The in-app wizard does, with consent.

## Alternatives considered

| Option | Why not |
|---|---|
| Native app per OS (Electron, Tauri, Swift) | Doubles the surface for a web UI, adds an auto-updater, needs signing anyway. A launcher is enough. |
| Docker Desktop required everywhere | Paid for larger organisations. Default only where easiest; Colima and Docker CE in WSL 2 are the free routes. |
| WiX MSI | Open Source Maintenance Fee, fails ADR 0013. |
| MSIX | Signing mandatory, sandbox fights Docker. |
| `.deb`, `.rpm`, Flatpak, Snap | Repo hosting and a key to guard, still need Docker. |
| `get.docker.com` inside our installer | Docker says testing and development only. |
| A minisign or GPG release key | A private key with no good home (§9). |
| A macOS VM on this Fedora laptop or the Windows PC | Apple's licence allows macOS VMs only on Apple hardware (§6). |
| A self-hosted CI runner on the owner's data laptop | Public repo: it would run pull-request code beside real statements (B6). |
| Run without Docker (venv plus system Postgres, Redis, Caddy) | Four services per distro and none of the hardening survives. |
| An appliance OS image | The right answer for a dedicated box; a project of its own. |

## Comparable projects (read 2026-11-22)

- **Immich:** a compose file plus `.env`, `docker compose up -d` (https://docs.immich.app/install/docker-compose). No installer or signing.
- **Coolify:** `curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash` on its own CDN, "review the script first"
  (https://github.com/coollabsio/coolify). The one-liner norm, with no signature check. We do better in §8.
- **Actual Budget:** Docker server plus desktop apps for Windows, Mac and Linux from GitHub (https://actualbudget.org/docs/install/).
- **Nextcloud AIO:** one `docker run` plus Docker Desktop guides for Windows and macOS (https://github.com/nextcloud/all-in-one).
- **Umbrel, Home Assistant, Firefly III:** OS images (Umbrel, HA) and a compose install (Firefly III). Not read in detail: UNVERIFIED.

## Consequences

- The first usable run (a stranger installs on Windows or Mac and sees the app) needs the local ingress and the wrapper, so
  those are in this plan (IN-A1, IN-A2), not deferred to Phase 8.5. Until IN-A1 lands, an install delivers a "preview" with
  Chrome's certificate warning and no passkeys.
- `release.yml` gets heavier (arm64, bundle, installers, signatures, tag guard). It is a protected path (Bulwark) and a
  public repo means standard runners are free, with the Free plan's 20 concurrent jobs (5 macOS) as the real limit.
- ADR 0013's private-repo minute arithmetic is stale for standard runners now the repo is public; Keel or Harbor should amend
  it. The "nothing paid by default" principle stands.
- macOS ships unsigned first and has no real-hardware stack test until a Mac is available. Windows stack tests run on the
  owner's personal PC by hand, never through a self-hosted runner.
- A backup-first update path needs `holdfast backup` and `restore` earlier than Phase 9. IN-B1 builds the local, host-side
  part; Phase 9's offsite layer sits on top.

## Verification state

Facts read from primary sources on 2026-11-22 carry their URL. **UNVERIFIED:** Apple's macOS licence wording on VMs (PDF not
machine-readable here), Docker Desktop reset-menu names, Azure Artifact Signing price, winget IDs and unsigned-installer
moderation, Podman and Colima licences, rootless Podman below port 1024, `!override` under podman-compose, Mark-of-the-Web with
`irm`, quarantine and `curl`, macOS 15's removed bypass, Apple Silicon runner nesting, Intel macOS runner retirement date
(the `macos-15-intel` and `macos-26-intel` labels exist today; macOS 14 images retired 2026-11-02 per the runner-images
README), Colima's Docker version, a Pi's memory floor, and whether SignPath accepts an alpha pre-release.

## Gadfly response (revision 1, PR #75)

| Item | Disposition |
|---|---|
| B1 repo is public | **Accepted.** Verified `PUBLIC`. Q8 deleted, public-repo gates removed, arm64 runner used now, SignPath gated on "released", budget section rewritten (plan §3). |
| B2 uninstall deletes the key | **Accepted.** State directory separate from code, pinned project name, `holdfast keys` refuses to overwrite, uninstall keeps state, reinstall-decrypts test (§2, §11, IN15). |
| B3 optional backup, no rollback | **Accepted.** Mandatory verified host backup, N-1 kept, rollback is restore, one-way list, Postgres major by dump and restore, release gate (§11, IN15). |
| B4 data in a VM disk | **Accepted.** Host-side scheduled backups on by default on Windows and macOS, location stated, host-path test (§2, IN-B1). |
| B5 critical path | **Accepted.** PA-D2 and PA-D6 pulled in as IN-A1 and IN-A2; PA-D6 marked superseded in ADR 0021; first step is an owner-hardware spike (plan "Slice order"). Podman and the distro long tail moved down. |
| B6 self-hosted runner | **Accepted.** Forbidden on any machine with real data; a dedicated or ephemeral machine needs its own ADR and a Bulwark verdict first (plan "CI matrix"). |
| S1 hash on another origin | **Accepted.** GitHub-URL one-liner, hash in the Release notes, Cloudflare called the code-execution root, DNSSEC and CAA in Q7, function-wrapped stub (§4, §8). |
| S2 tags by agents | **Accepted.** Tag ruleset, `main`-reachability and `ci-required` guard, protected environment, residual risk stated (§9, IN-T1). |
| S3 freeze attack | **Accepted.** Embedded minimum version and a signed `CHANNEL` asset (§8, IN-T2). |
| S4 secrets contradict §9 | **Accepted.** Protected environment, the re-introduced stealable key recorded (§9). |
| S5 Docker Desktop for novices | **Accepted.** Preflight, `holdfast open` launcher on every OS, first-launch step, RAM floors, AV note, step count +1 (§3, §5, §6, IN10, IN12, IN-O1). |
| S6 rule 10 detail | **Accepted.** TLS 1.2, MSI fallback, 5.1 CI job, one size (80), scoped text, `Unblock-File` (§5, IN10). |
| S7 secrets shim | **Accepted.** stdout from the container, host shim writes with umask or `icacls`, recovery-kit step (§2, IN3). |
| S8 CI claims | **Accepted.** Floor job renamed to a refusal test; `--install-docker` real run moves to owner VM; Windows stack assumed "no" on runners; Intel retirement noted; `ubuntu-26.04` confirmed as a listed GA label (plan "CI matrix"). |
| S9 Inno licence | **Accepted with a finding.** Quoted verbatim: commercial use is permitted; purchase is a request. Residual risk recorded under ADR 0013 (§5). |
| S10 SignPath dual-licensing | **Accepted.** Asked in the application; allowlist test that no Cloud-only code is signed (§5). |
| S11 apex `install.sh` | **Resolved by fact.** Both URLs return 404 on 2026-11-22; nothing is deployed; wording fixed (§4). |
| S12 PATH | **Accepted.** Stub checks PATH, offers the rc line with consent (§7, IN7, IN12). |
| N1 to N5 | Added to the plan's owner questions (N1 upgrade-path gate, N2 key-ring escrow, N3 audience, N4 support channel, N5 who may tag). |
