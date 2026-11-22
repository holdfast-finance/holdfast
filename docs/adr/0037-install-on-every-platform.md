# 0037. Install on every platform: one verified path per OS, short endpoints on our own domain

- Status: **Proposed**
- Date: 2026-11-22
- Deciders: Harbor (DevOps) drafts; Lattice and Bulwark review; Keel decides. The owner asked for it
  (quoted: "right now we only have ubuntu support we need to ensure other distros, a windows exe and a
  pwsh command and macos dmg or package and then terminal command").
- Reviewers needed: Bulwark (every download-and-run path, signing keys), Gadfly (challenge before
  acceptance), Lattice (the release bundle contract, §2).
- Read first: ADR 0018 (release artifacts), ADR 0021 (zero-config private access and the Docker floor),
  ADR 0013 (free-tier CI), ADR 0009 (Cloudflare Pages note). Numbering: 0036 is PR #70, so this is 0037.
- Nothing here is built. `docs/planning/INSTALL-PLAN.md` holds the slices, and its "Owner questions" section lists what blocks each slice.

## Context

### What exists today (read from the repo on 2026-11-22)

The honest finding is that **the repo contains no Ubuntu-specific code**: no `apt`, no `systemd`, no
distro paths. "Only Ubuntu" is really four other facts:

1. **The install is the dev stack, from a clone.** README and `docs/releases/v0.0.0-alpha.md` say
   `git clone`, then `make keys && make up && make migrate`. `make up` runs `docker-compose.dev.yml`:
   hot reload, a `node:24-alpine` container running Vite, `./backend` mounted over `/app`. Every base
   service has a `build:` entry, so the user's machine compiles the images. Nothing pulls the
   GHCR images that `release.yml` publishes. There is no production-style compose file for a stranger:
   `infra/docker-compose.prod.yml` needs `TAILSCALE_BIND_IP`, `APP_HOST` and a Cloudflare DNS-01 token
   (a power-user path).
2. **Host tools the user must already have:** `git`, GNU `make`, `bash`, `python3` (for
   `scripts/gen-keys.py` and `check-compose-ports.py`), `curl`, and for `make test`, `uv` and `pnpm`.
   `make` does not exist on stock Windows or macOS. `dev.ps1` mirrors the Makefile (hard rule 10) but
   still needs `python3`, which on Windows is often the Microsoft Store stub.
3. **The Docker floor (ADR 0021 §2): Engine 28.3.3.** Distro packages differ: Fedora 43 and Ubuntu 24.04
   with `noble-updates` meet it, Debian 12 and 13 and Raspberry Pi OS do not (`docker.io` 20.10.24 and
   26.1.5, ADR 0021 §7). Checked here, 2026-11-22: GitHub's `ubuntu-24.04` runner image ships Docker
   Server **28.0.4**, below the floor, while `ubuntu-26.04` ships **29.4.2** and Podman 5.7.0
   (https://raw.githubusercontent.com/actions/runner-images/main/images/ubuntu/Ubuntu2404-Readme.md and
   `.../Ubuntu2604-Readme.md`, read 2026-11-22). A CI job on `ubuntu-latest` would trip the floor.
4. **It has only ever run on Linux, and on amd64.** CI is `ubuntu-latest` only. `release.yml` builds
   `platforms: linux/amd64` (line 180), so Raspberry Pi and Apple Silicon (native arm64 VM) get no
   native image. PHASE-0's blocked item says `./scripts/dev.ps1 up` has never run on a Windows host with
   Docker Desktop (`docs/phases/PHASE-0.md`, "Blocked: what the owner must do"). The v0.0.0-alpha notes
   say the same ("has not been run on a real Windows machine yet").

Not a problem (checked): `.gitattributes` forces `eol=lf` for everything but `*.ps1`, so a Windows
checkout will not hand CRLF scripts to Linux containers. `dev.ps1` targets pwsh 7 and parses today.

Other portability hazards found in the compose files (each becomes a slice or spike in the plan):
- `./data/documents` is a **bind mount** that `make up` pre-chowns to uid 10001 with a root helper
  container (`docs-dir` in the Makefile). On Docker Desktop (Windows paths, macOS virtiofs) ownership
  semantics differ (UNVERIFIED), and on SELinux hosts with Podman a bind mount needs the `:z` label.
- Compose uses the `!override` and `!reset` merge tags. They need Docker Compose v2.24+ and are unlikely
  to work in `podman-compose` (UNVERIFIED), so Podman must go through the real `docker compose` plugin.
- Caddy publishes `127.0.0.1:443`. Rootless Podman cannot bind ports below 1024 by default
  (`net.ipv4.ip_unprivileged_port_start`, UNVERIFIED on 5.x). ADR 0021 moves the local site to
  `127.0.0.1:47800`, which removes the problem and the self-signed-certificate warning, and Chrome
  disables WebAuthn on certificate errors, so **a usable installer depends on ADR 0021's local ingress.**
- No `holdfast` wrapper exists yet. ADR 0021 plans one (`up`, `update`, `backup`, `recover`) and an
  installer that compares `docker version`.

### Constraints

- **Hard rule 12:** no public port. Caddy binds loopback (or the Tailscale IP). Installers never open a
  firewall port or ask for port-forwarding.
- **Hard rule 10:** Windows scripts target PowerShell 7. §5 proposes one narrow exception, for Keel to rule on.
- **Hard rule 9:** every new external service is named in §10.
- **Hard rule 3 and ADR 0018 item 5:** no telemetry, no update ping, no secret ever printed. Installers
  write `.env` (mode 0600) and never echo it.
- **ADR 0013:** no paid plan or licence for CI/CD. The owner has asked that nothing cost money until
  there is revenue. Anything below that costs money is flagged, and the free path is the default.
- **ADR 0005:** AGPL-3.0-only. Anything we bundle or link must be compatible.
- Holdfast is a Docker Compose stack of seven services. An installer is therefore: check or install a
  container runtime, fetch a verified release, generate secrets, start the stack, open the browser, offer
  phone access (ADR 0021). It is not a native application.

## Decision

### 1. One core, thin per-OS shells

All platforms share one **release bundle** and one **`holdfast` command** (the ADR 0021 wrapper). The OS
installers only get a runtime in place, put the bundle on disk, and call `holdfast up`. This keeps
`make` and `dev.ps1` parity (the dev targets stay for contributors) and means a bug is fixed once.

### 2. The release bundle (the missing artifact)

Each GitHub Release gains, next to the wheel and sdist:

| Asset | Contents |
|---|---|
| `holdfast-<ver>-bundle.tar.gz` (and `.zip` for Windows) | `compose.yaml` (release stack: images pinned by `@sha256`, no `build:`, no dev override, no bind-mounted source), `Caddyfile`s, `postgres/init/00-roles.sh`, `.env.example`, `holdfast` (POSIX sh), `holdfast.ps1`, `VERSION` |
| `SHA256SUMS` | one line per asset, covering the bundle, installers, wheel, sdist and both stub scripts |
| `SHA256SUMS.sigstore.json` | a Sigstore bundle: keyless signature of `SHA256SUMS` by `release.yml` (§9) |
| `install.sh`, `install.ps1` | the bootstrap stubs (§4), also attached so they can be verified |
| platform installers | `holdfast-<ver>-setup.exe`, `holdfast-<ver>.pkg` (§5, §6) |

Images are multi-arch (`linux/amd64,linux/arm64`) from the first installer release. That needs
`release.yml` to build arm64 (QEMU, or the free `ubuntu-24.04-arm` runner on a public repo, UNVERIFIED for
private repos). The documents volume becomes a **named volume**, initialised by a one-off container, so
the install needs no host-side `chown`, no `:z` label, and no NTFS or virtiofs ownership assumption. The
cost: reading stored documents on the host needs `docker cp` or `holdfast export`, which ADR 0021 already
plans. (Alternative kept in the plan: keep the bind mount and add `:z` and `:U`; spike IN-L-S2 decides.)

`gen-keys` runs **inside the api image** (`holdfast keys`), so no host Python is needed on any OS. Output
goes straight to `.env` with umask 077, never to the terminal.

### 3. Runtime per platform

| Platform | Default runtime | Why | Alternatives (tier) |
|---|---|---|---|
| Linux | **Docker Engine ≥ 28.3.3** (Docker CE from Docker's repo, or the distro package if it meets the floor) | The stack and ADR 0021's floor were built and verified against Docker. | Rootless/rootful Podman 5 with the real `docker compose` plugin pointed at `podman.sock`: **Tier 2, behind spike IN-L-S1.** `podman-compose`: not supported (merge tags). |
| Windows | **Docker Desktop with the WSL 2 backend** | The only first-party GUI runtime, per-user install needs no admin (below). | Docker CE inside a WSL 2 distro (no Desktop licence at all): Tier 2, spike IN-W-S1. Podman Desktop, Rancher Desktop: Tier 3, documented only. |
| macOS | **Docker Desktop** | Same reason, and ADR 0021's step counts assume it. | **Colima** (MIT, `brew install colima docker docker-compose`): Tier 2 and the scripted no-licence path. OrbStack: allowed, never scripted (free for personal use only, $8 per user per month for business, https://orbstack.dev/pricing, read 2026-11-22). Rancher Desktop: Tier 3. |

Tier 1 means CI or an owner machine proves it before release. Tier 2 means a documented path with a
spike behind it. Tier 3 means "may work, not tested".

Facts behind the table (all read 2026-11-22):
- **Docker Desktop licence.** Free for personal use, small businesses (fewer than 250 employees and under
  $10M annual revenue), education and non-commercial open source. Paid for professional use in larger
  organisations and government (https://docs.docker.com/subscription/desktop-license/). Holdfast is a
  personal, AGPL self-host product, so most users are free. **The installer never installs Docker Desktop
  silently.** It prints that sentence, links the licence, and installs only on an explicit yes. Anyone who
  falls on the paid side is pointed at Colima or Docker CE in WSL 2. We do not decide eligibility for them.
- **Windows needs WSL 2** (or Hyper-V on an all-users install). The per-user Docker Desktop install uses
  WSL 2, needs no admin rights, and cannot run Windows containers
  (https://docs.docker.com/desktop/setup/install/windows-install/). `wsl --install` needs an elevated
  prompt, Windows 10 build 19041 or newer, and a **reboot**
  (https://learn.microsoft.com/en-us/windows/wsl/install). The installer must survive the reboot (§5).
- **Docker Engine on Linux** supports Fedora 43/44/45, Ubuntu 22.04/24.04/26.04/26.10, Debian, RHEL,
  CentOS, Rocky, AlmaLinux, Oracle Linux and Raspberry Pi OS (32-bit marked unsupported-ish), on amd64 and
  arm64 (https://docs.docker.com/engine/install/ and `/fedora/`, `/ubuntu/`). The `get.docker.com`
  convenience script is "only recommended for testing and development environments"
  (https://docs.docker.com/engine/install/ubuntu/). **We do not use it.** `install.sh --install-docker`
  follows the documented per-distro repository steps, with a printed plan and a prompt.
- **The `docker` group is root-equivalent** (https://docs.docker.com/engine/install/linux-postinstall/).
  The installer says so before adding the user and offers `sudo` per command instead.
- **Podman**: `podman compose` is a thin wrapper around an external provider such as docker-compose
  (https://docs.podman.io/en/latest/markdown/podman-compose.1.html). Podman Desktop supports the Compose
  specification (https://podman-desktop.io/docs/compose). Rancher Desktop is open source
  (https://rancherdesktop.io/), Colima is a macOS/Linux runtime project
  (https://github.com/abiosoft/colima). Licences for Podman (Apache-2.0) and Colima (MIT) are from
  memory: UNVERIFIED, confirm in the spike.
- **SELinux.** Docker's bind-mount docs list the `z` and `Z` options for SELinux labels
  (https://docs.docker.com/engine/storage/bind-mounts/). On this owner's Fedora 43 box (`getenforce`
  = Enforcing) the Docker daemon reports no `selinux` security option, so Docker does not label mounts
  and the stack works unlabelled. Podman does label, so the Podman tier needs `:z` on read-only config
  mounts (never on `$HOME`) or the named-volume change in §2. cgroups v2 is in use here (`cgroup2fs`); old
  cgroups v1 hosts are out of scope (UNVERIFIED which supported distros still default to v1).
- **The Docker floor applies on every OS** (ADR 0021). Docker Desktop 4.100.0 bundles 29.9.3 (ADR 0021).
  A Colima VM's engine version is whatever its image ships and must be checked (UNVERIFIED), and the
  installer refuses `up` below the floor exactly as ADR 0021 says.

### 4. Short endpoints on our own domain, artifacts stay on GitHub

```
https://get.holdfastfinance.app/install.sh     POSIX sh stub (Linux, macOS)
https://get.holdfastfinance.app/install.ps1    pwsh stub (Windows, also runs on pwsh for Linux/macOS)
https://get.holdfastfinance.app/latest.json    {"version","prerelease","bundle","sha256sums","sigstore"} (URLs into GitHub)
https://get.holdfastfinance.app/latest         302 to the newest Release page on GitHub
https://get.holdfastfinance.app/win            302 to the newest setup.exe asset
https://get.holdfastfinance.app/mac            302 to the newest .pkg asset
```

**Cloudflare mechanism: an assets-only Worker with a custom domain** (`holdfast-get`, route
`get.holdfastfinance.app`), the pattern `docs/ops/domain-email-pages.md` already records for
`cloud.holdfastfinance.app`. Reasons and numbers:
- Static-asset requests are free and unlimited on Workers, and the 100,000 requests per day cap applies to
  Worker script invocations, which an assets-only Worker has none of
  (https://developers.cloudflare.com/workers/platform/pricing/ and `/limits/`, read 2026-11-22).
- `_redirects` gives status-coded redirects: 2,000 static and 100 dynamic rules per file
  (https://developers.cloudflare.com/workers/static-assets/redirects/), far more than six lines need.
  `_headers` sets `Content-Type: text/plain; charset=utf-8`, `X-Content-Type-Options: nosniff`,
  `Cache-Control: no-cache` for the stubs, so a browser shows the script instead of running or caching it.
- Cost on the free plan: $0. A custom domain on a zone we own needs no paid feature. (Whether the Workers
  free plan asks for a card is UNVERIFIED; the existing Cloud preview Worker shows it is already usable.)
- `holdfastfinance.app` is on the HSTS preload list (`.app` TLD, ops doc), so a plain-http `curl` cannot be
  downgraded in a browser. For `curl` it is still our `-fsSL --proto '=https' --tlsv1.2` that guarantees it.

Not chosen, and why:
- **R2.** Not needed (artifacts stay on GitHub Releases). It is also **not enabled on the account**:
  `cf r2 buckets list` returned `[10042] Please enable R2 through the Cloudflare Dashboard` on
  2026-11-22. Free tier is 10 GB-month, 1M Class A and 10M Class B operations, free egress
  (https://developers.cloudflare.com/r2/pricing/). Revisit only if GitHub download limits bite.
- **Cloudflare Pages.** Works, but is a second deploy tool for the same job, and ADR 0009 plans Pages for
  the Cloud site only. The Worker already has a deploy recipe in the Cloud site repo.
- **GitHub Pages on the apex site.** Free and already deployed (`/install.sh` on `holdfastfinance.app`),
  but a Pages site has one custom domain, so `get.` cannot be a second host, and we get no `_headers`.
  Kept as the fallback if Cloudflare is ever unavailable: the stub text is also served from the apex.
- **A `curl|sh` straight to `raw.githubusercontent.com`.** Works, ugly, not memorable, and `main` moves.

**The endpoint is not a trust root for the artifacts.** `latest.json` and the redirects only point at
GitHub Release assets. Every byte that runs is verified by the stub (§8) against `SHA256SUMS`, and
`SHA256SUMS` against a Sigstore signature whose identity is pinned in the stub. A compromised Cloudflare
account can serve a malicious **stub**, and that is the real exposure (§8 says how we bound it). It cannot
forge a release signature.

**Deploying it.** v1: Keel runs `cf` (no Wrangler config exists in this repo, so the owner's rule says use
`cf`) after cutting a release, from a small `infra/get-site/` directory (stubs, `_headers`, `_redirects`,
`latest.json`), recorded in RELEASES.md. No Cloudflare token enters GitHub. Later: a release-workflow job
with a scoped Worker-edit token behind a protected environment, if the owner wants it (owner question 6).
**No Cloudflare resource is created by this ADR or by the first install-plan slices** (plan, "Owner questions").

### 5. Windows

- **Format: Inno Setup `.exe`.** Free to use and redistribute (https://jrsoftware.org/isinfo.php, read
  2026-11-22: commercial users are asked to buy a licence; Holdfast is AGPL and free). The WiX Toolset now
  states that use "requires an Open Source Maintenance Fee"
  (https://raw.githubusercontent.com/wixtoolset/wix/main/README.md, read 2026-11-22), which fails
  ADR 0013. MSIX needs a trusted certificate to install at all and its sandbox is a poor fit for a tool
  that drives Docker. Inno Setup is preinstalled on GitHub's `windows-2025` image, so CI needs no download.
- **What the .exe does:** per-user install into `%LOCALAPPDATA%\Programs\Holdfast` (no admin), then checks
  for a runtime. If none, it explains the Docker Desktop licence, offers `winget install -e --id Docker.DockerDesktop`
  (winget IDs UNVERIFIED), and if WSL 2 is missing, asks for elevation once and registers a
  **resume-after-reboot** step (a RunOnce key that relaunches the installer's second phase). Then it runs
  `holdfast up` and opens `http://localhost:47800/setup#t=<token>` (ADR 0021). Silent mode
  (`/VERYSILENT`) never installs Docker. It exits with a code that says what is missing.
- **PowerShell 7 and hard rule 10.** The `.exe` bundles nothing but `holdfast.ps1` and requires `pwsh`; it
  installs PowerShell 7 with `winget install --id Microsoft.PowerShell` on a prompt. The one-line command
  has a chicken-and-egg problem, since a stock Windows has only 5.1. **Proposal for Keel:** amend rule 10 to
  allow a bootstrap `install.ps1` stub written for 5.1 and 7 (about 60 lines, no features), whose only job is
  to get `pwsh` and re-run the verified core under it. Without the amendment the Windows one-liner is
  two lines (install pwsh, then run). ADR 0021's step counts already assume the 4-step version. The amendment
  touches CLAUDE.md, so Keel decides and records it.
- **One-line command (works in pwsh 7):**
  `irm https://get.holdfastfinance.app/install.ps1 | iex`
  The stub downloads the versioned core from the GitHub Release, verifies it (§8), and only then runs it.
  It is short enough to read on the website before running, and the site shows its SHA-256.
  `irm` (Invoke-RestMethod) does not set the Mark-of-the-Web, so SmartScreen does not gate this path
  (widely reported, UNVERIFIED here). **That makes our own verification the only check on this path**, which
  is why it is mandatory and has no skip flag.
- **winget as the verified one-liner:** `winget install HoldfastFinance.Holdfast` once a manifest is merged
  into `microsoft/winget-pkgs`. winget checks the installer hash from the manifest
  (`winget hash` exists, https://learn.microsoft.com/en-us/windows/package-manager/winget/). Whether winget
  moderation accepts an unsigned installer is UNVERIFIED. A Chocolatey or Scoop package adds nothing we
  can verify and is deferred.
- **Signing (Authenticode).** Cost and eligibility, read 2026-11-22:
  - **SignPath Foundation: free for open source.** Rules include an OSI licence **without commercial
    dual-licensing**, no proprietary component, an actively maintained and **already released** project,
    and building and signing only your own source (https://signpath.org/terms). Holdfast qualifies on
    licence, but the **public repo and a first release must exist first**, and Gadfly should rule whether
    the paid Holdfast Cloud (ADR 0009) reads as "commercial dual-licensing" (it is AGPL hosting, not a
    second licence, so we think not).
  - **Azure Artifact Signing** (renamed from Trusted Signing): Public Trust is open to organisations in
    the US, Canada, EU, UK, Australia, NZ, Japan, South Korea, Singapore, Switzerland, Norway and Israel,
    and to **individual developers only in the US or Canada**; it needs an Azure subscription and an identity
    validation (https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart). Basic is 5,000
    signatures a month. **The monthly price did not render on the pricing page** (it showed `$-`), so I
    recall about $10 a month and mark it UNVERIFIED.
  - A traditional OV/EV code-signing certificate from a CA costs money every year and, since 2023, the
    key must live on a hardware token or HSM (UNVERIFIED), which a GitHub-hosted runner cannot hold. Rejected.
  - **SmartScreen reputation** builds from download history even for signed files
    (https://learn.microsoft.com/en-us/azure/artifact-signing/faq). A new publisher gets warnings for a
    while whatever we do.
  - **Decision:** apply to SignPath after the repo is public (cost: $0, effort: an application). Azure
    Artifact Signing is the paid fallback only if the owner approves a monthly fee.
- **Unsigned interim (until a certificate exists).** The `.exe` is published with a SHA-256 and the signed
  `SHA256SUMS`. A double-click shows "Windows protected your PC", then More info, then Run anyway. The
  release notes say so in the first lines and give the verify command:
  `(Get-FileHash .\holdfast-<ver>-setup.exe -Algorithm SHA256).Hash` compared with `SHA256SUMS`, plus the
  `cosign verify-blob` line for `SHA256SUMS`. The recommended interim path is the `irm | iex` stub, which
  verifies for the user.
- **Uninstall:** the standard Apps and Features entry runs `holdfast uninstall`: stack down, volumes kept,
  files removed. It never removes Docker or WSL.

### 6. macOS

- **Format: a signed, notarized `.pkg`** (built with `pkgbuild` + `productbuild`), not a `.dmg`.
  A `.dmg` only earns its keep when it carries a drag-to-install `.app`, and Holdfast has no native app: the UI
  is the browser. The `.pkg` installs `/usr/local/bin/holdfast` and a small `Holdfast.app` launcher
  (start the stack, open the browser) in `/Applications`. A `.dmg` wrapper is deferred, not refused: it is a
  small addition on top of a notarized `.pkg` if the owner wants the familiar window. (Owner question 5.)
- **One-line command, same stub as Linux:**
  `curl -fsSL --proto '=https' --tlsv1.2 https://get.holdfastfinance.app/install.sh | sh`
  It must run under macOS's bash 3.2 and BSD tools (`shasum -a 256`, not `sha256sum`), so it is POSIX `sh`
  and shellcheck-clean. Files fetched by `curl` carry no quarantine attribute, so Gatekeeper does not gate a
  shell script (widely known, UNVERIFIED here; the macOS CI job asserts it).
- **Homebrew tap, the verified one-liner:** `brew install holdfast-finance/tap/holdfast` from our own tap
  (a GitHub repo, `homebrew-tap`). A **formula** (CLI) avoids Gatekeeper entirely and is checksum-pinned by
  Homebrew. A **cask** for the `.pkg` in the *official* homebrew-cask would require the artifact to pass
  Gatekeeper: "must pass Homebrew's Gatekeeper checks" (https://docs.brew.sh/Acceptable-Casks, read
  2026-11-22), so it waits for notarization. Our own tap is not bound by that rule, but a cask of an
  unnotarized pkg would still be blocked by the OS.
- **Notarization needs an Apple Developer Program membership: $99 a year**
  (https://developer.apple.com/programs/, read 2026-11-22), a Developer ID Application and Developer ID
  Installer certificate, and `notarytool` credentials as CI secrets. It needs an **owner Apple account**
  (an organisation enrolment also needs a D-U-N-S number, UNVERIFIED). Without it, a downloaded `.pkg`
  is blocked and the user must use System Settings, Privacy & Security, "Open Anyway"
  (https://support.apple.com/en-us/102445). On macOS 15 the old control-click "Open" bypass for unsigned
  apps was removed (UNVERIFIED here), so we do not tell anyone to rely on it.
- **Unsigned interim:** ship the one-line command and the Homebrew formula only; attach no `.pkg` until it is
  notarized. This is a real simplification: nothing unsigned is ever double-clickable.
- **Runtime:** Docker Desktop or Colima (§3). `install.sh` checks `docker version` against the floor;
  on a Mac with no runtime it offers `brew install --cask docker` (UNVERIFIED cask name for current Docker
  Desktop) or Colima, never silently. Apple Silicon runs the arm64 images natively once §2 lands; amd64-only
  images would run under emulation, which is too slow for Postgres, so the arm64 build is a prerequisite.
- **Uninstall:** `holdfast uninstall` (the `.pkg` has no uninstaller), which also runs `pkgutil --forget`.

### 7. Linux and other Unix: one script, no packages yet

- **`install.sh`** (POSIX `sh`, shellcheck, no bashisms): reads `/etc/os-release`, finds Docker or Podman,
  compares the version with the floor, and on `--install-docker` follows Docker's documented repository
  steps for **Debian/Ubuntu (apt)**, **Fedora (dnf)**, and **RHEL, CentOS Stream, Rocky and AlmaLinux (dnf)**
  (raspberry Pi OS 64-bit follows Debian). Anything else (Arch, openSUSE, Alpine, NixOS, NAS firmware) gets
  a precise message and the manual-install link, which is also ADR 0021's NAS statement.
- It installs to `${XDG_DATA_HOME:-$HOME/.local/share}/holdfast`, links `holdfast` into `~/.local/bin`,
  and needs `sudo` only for the Docker install and the `docker` group. No systemd unit is installed in v1: Compose
  `restart: unless-stopped` plus Docker's own boot start covers a restart. An optional `holdfast autostart`
  can be a later slice (UNVERIFIED need). cgroups v2 and a kernel with the usual namespace support are
  assumed (every distro above).
- **No `.deb`, `.rpm`, Flatpak or AUR package from us.** A package would only wrap this script, and a
  signed apt or dnf repository is a new hosting service and key to guard. Flatpak cannot run a Docker
  daemon. An AUR `PKGBUILD` is welcome from the community and is not ours to maintain. Revisit when
  there is demand.
- **arm64 and Raspberry Pi:** supported once §2's multi-arch images exist, 64-bit OS only. The stack
  needs about 1.5 GB of RAM (UNVERIFIED, measure), so a 2 GB Pi is the floor and a 4 GB Pi is
  recommended.
- **Podman on Fedora (this owner's machine has Docker and podman sockets):** after spike IN-L-S1, `install.sh`
  accepts a Podman setup that provides the `docker compose` plugin against `podman.sock`, rootful or
  rootless, with the labelling in §3. Until then it detects Podman and says "not yet supported" instead of
  proceeding.

### 8. How the one-liners avoid "pipe unverified code"

The one-liner is `stub | sh`. The stub never runs unverified code beyond itself:
1. The stub is tiny (target under 80 lines), pure fetch-verify-exec, and everything it needs is pinned inside
   it: the GitHub repo, the Sigstore signing identity, and the SHA-256 of the `cosign` binary per OS and
   architecture it may download.
2. It downloads `SHA256SUMS` and `SHA256SUMS.sigstore.json` from the **GitHub Release**, a different origin
   from `get.holdfastfinance.app`, resolving the version from `latest.json` or the `--version` flag.
3. It verifies the signature (`cosign verify-blob --bundle ... --certificate-identity-regexp
   '^https://github.com/holdfast-finance/holdfast/\.github/workflows/release\.yml@refs/tags/v'
   --certificate-oidc-issuer https://token.actions.githubusercontent.com`, flags from
   https://docs.sigstore.dev/cosign/verifying/verify/, read 2026-11-22), then checks the bundle and installers
   against `SHA256SUMS`. A mismatch deletes the download and exits non-zero. There is **no `--insecure`
   flag**.
4. Only then does it unpack the bundle and run the bundled core, which does everything else.
5. A **downgrade guard**: the stub refuses a version lower than the one installed, or older than the
   newest signed `latest` it can see, unless `--version` is given explicitly.
6. A **paranoid path**, documented first on the install page: download the bundle, `SHA256SUMS` and bundle
   file with `gh release download` or a browser, run the two verify commands by hand, then `./install.sh --local`.

What this does **not** solve, said plainly: the stub itself comes from `get.holdfastfinance.app`. Someone
who controls our Cloudflare account or its deploy credentials can swap the stub. Bounds: (a) the stub is a
signed release asset too, and the install page, served from GitHub Pages (a different origin and account
path), prints its SHA-256 so a careful user compares; (b) `latest.json` and redirects cannot change what the
stub accepts; (c) Cloudflare account hardening (hardware-key 2FA, a dedicated Workers token, no shared
account with unrelated projects) is an owner item, and the account holds other, unrelated projects today,
so a **separate Cloudflare account for Holdfast** is recommended before launch (owner question 7).
The hard-rule-3 privacy note: the endpoint sees the requester's IP like any web server. We keep no logs
(Workers Logs off, no Web Analytics) and the app never calls it (ADR 0018 item 5).

### 9. Signing and provenance of the artifacts themselves

- **Images and `SHA256SUMS` are signed keyless with Sigstore from `release.yml`.** This is the "signed
  images before the first beta" requirement in ADR 0018 item 6, and the installer's trust anchor.
  Sigstore's public-good service is already named under rule 9 by ADR 0018. The identity in §8 is that
  workflow on a `v*` tag.
- GitHub artifact attestations use the same public-good instance on public repositories (read
  2026-11-22); whether private repositories can use them on the Free plan is UNVERIFIED, so the plan does
  not depend on them.
- **Why not a minisign or GPG key:** it would need a private key somewhere. The only places are an
  Actions secret (the very thing a CI compromise steals) or Keel's machine (a single point of failure and
  a manual step per release). Keyless ties trust to "this repository's release workflow on a tag".
  Cost of keyless: the stub must fetch `cosign` (pinned by hash) and needs Sigstore's TUF root online.
  A verifier with no extra binary (OpenSSL ECDSA, `.NET ECDsa`) is a possible later fallback.
- **Windows and macOS code signing are separate** from this (Authenticode and Developer ID, §5 and §6) and
  exist so the OS stops warning, not so we can trust the artifact.

### 10. External services (hard rule 9)

New or newly relied-on by this ADR. Existing ones are listed so the set is complete.

| Service | Role | New? | Cost |
|---|---|---|---|
| **Cloudflare**: Registrar, DNS for `holdfastfinance.app`, Email Routing, **Workers (static assets) for `get.` and the Cloud preview** | Short install endpoints | Registrar, DNS, Email Routing and the Cloud-preview Worker are **already live but not named in any ADR**: `docs/ops/domain-email-pages.md` says Workers are "covered by ADR 0009", and ADR 0009 only says "Cloudflare Pages" for the Cloud site. ADR 0002 is the product-name decision and does not mention Cloudflare. **This ADR names all of them.** | $0 (domain at-cost already paid) |
| Cloudflare **R2, KV, D1, Queues, Pages, Workers with code** | not used | Each would need its own ADR line before use. R2 is not enabled on the account. | n/a |
| GitHub Releases, GHCR, GitHub Pages | Artifact hosting, images, site | Existing (ADR 0018, ops doc) | $0 |
| Sigstore (Fulcio, Rekor, TUF root) | Keyless signing and verification | Existing (ADR 0018 item 6). New at **install time**: the user's machine contacts it when verifying | $0 |
| `download.docker.com` (Docker's apt and dnf repositories), Docker Desktop installer | Fetched by the installer on explicit consent | New, install-time only, user-consented | $0 (Desktop licence terms in §3) |
| Homebrew (`homebrew/core` bottles, our own `homebrew-tap` GitHub repo) | macOS install channel | New, optional | $0 |
| `winget` and `microsoft/winget-pkgs`, WSL download, Microsoft Store (PowerShell) | Windows install channel | New, optional | $0 |
| **SignPath Foundation** or **Azure Artifact Signing** | Windows Authenticode | **New, decided later** (only one is used) | $0 or a monthly fee (§5) |
| **Apple Developer Program, Apple notary service** | macOS Developer ID and notarization | **New, owner account needed** | $99 a year |
| `cosign` binary releases (GitHub, `sigstore/cosign`) | Verification tool the stub downloads, hash-pinned | New, install-time | $0 |

No installer or the app contacts any of these at run time except `holdfast update --check`, which is
user-initiated (no update ping, ADR 0018 item 5).

### 11. Update and uninstall

- **Update:** `holdfast update` (ADR 0021's wrapper): reads `latest.json` only when the user runs it, then does
  the §8 verification, pulls the pinned image digests, takes a backup if one is configured, runs migrations
  (the one-off `migrate` service, hard rule 8), and restarts. It refuses below the Docker floor and refuses a
  downgrade. There is no background updater, no auto-update, and no check on start. The alpha stays "no upgrade
  path before beta" (README) until a beta exists. Windows and macOS installers just re-run the same verified
  bundle on a newer version.
- **Uninstall:** `holdfast uninstall` stops the stack and removes the CLI and bundle directory, **keeping the Docker
  volumes** (the household's data and secrets). `--purge` removes the volumes only after typing the household
  name, after saying where the last backup is. It never uninstalls Docker, WSL, Colima or Tailscale. The Windows
  Apps entry and the macOS `holdfast uninstall` both call this.

### 12. Phone access (ADR 0021) per platform

The sidecar is a **compose overlay inside the same bundle**, so it is identical on every OS: `holdfast phone on`
(or the in-app wizard) starts the stock Tailscale container, userspace mode, no host Tailscale, no TUN, no
`NET_ADMIN`. Differences:
- **Linux:** needs Docker ≥ 28.3.3 for the loopback guarantee. Under rootless Podman the sidecar's userspace mode
  needs no extra capability (UNVERIFIED, part of IN-L-S1).
- **Windows and macOS (Docker Desktop, Colima):** the containers run in a VM, and published ports are forwarded to
  host loopback by the runtime. ADR 0021's verification of the "loopback only" claim was done on Linux. **It must
  be re-run on Docker Desktop for Windows and macOS** before the phone path is called supported there (plan IN-W3,
  IN-M3), because the runtime, not Holdfast, decides what a published port exposes.
- **Windows with Docker CE inside WSL 2:** `localhost` forwarding to the Windows side is on by default (UNVERIFIED).
  Phone traffic never needs it, since it arrives through the sidecar.
- No platform installer installs, configures or signs in to Tailscale for the user. The wizard does, in the app,
  with consent, and an installer never asks for a Tailscale key.

## Owner questions

Listed with cost and what each unlocks in `docs/planning/INSTALL-PLAN.md` section "Owner questions". The blockers:
Apple Developer Program, a Windows signing route (SignPath or Azure), Cloudflare account separation and who deploys
`get.`, the rule 10 amendment, and a Windows plus a macOS test machine that is not employer equipment.

## Alternatives considered

| Option | Why not |
|---|---|
| Native app per OS (Electron, Tauri, Swift) | Doubles the surface for a product whose UI is a web app, adds an auto-updater, and each needs signing anyway. A launcher is enough. |
| Docker Desktop required everywhere | Fails the "free for everyone" aim: paid for larger organisations. Kept as default only where it is the easiest, with Colima and Docker CE in WSL 2 as the free routes. |
| WiX MSI | Open Source Maintenance Fee, fails ADR 0013. |
| MSIX | Signing is mandatory, the sandbox fights Docker, store listing is out of scope. |
| `.deb`, `.rpm`, Flatpak, Snap | Need repo hosting and a signing key we would have to guard, and still need Docker. Deferred. |
| `get.docker.com` convenience script inside our installer | Docker says testing and development only. |
| A minisign or GPG release key | A private key to guard with no good home (§9). |
| Serve the install script from `raw.githubusercontent.com` | Not memorable, tracks a moving branch, no headers. |
| Run Holdfast without Docker (Python venv plus system Postgres, Redis, Caddy) | Four services to install per distro and none of the hardening (read-only root, dropped caps, internal networks) survives. Rejected; revisit only for a Raspberry-Pi-class appliance image. |
| An appliance OS image (Umbrel- or Home Assistant OS-style) | The right answer for a dedicated box, a large project of its own. Out of scope. |

## Comparable projects (what they do, to calibrate; read 2026-11-22)

- **Immich:** a downloaded compose file plus `.env`, then `docker compose up -d`
  (https://docs.immich.app/install/docker-compose). No installer, no signing story: the floor for "fine".
- **Coolify:** `curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash` on its own CDN, with a note that you
  can review the script first (https://github.com/coollabsio/coolify). The one-liner norm, with no signature
  check. We do better in §8.
- **Actual Budget:** Docker server, plus desktop apps for Windows, Mac and Linux downloaded from GitHub
  (https://actualbudget.org/docs/install/). A native-app route we rejected for cost.
- **Nextcloud AIO:** one `docker run` plus guides for Windows and macOS Docker Desktop
  (https://github.com/nextcloud/all-in-one). A single container that manages the rest.
- **Umbrel, Home Assistant, Firefly III:** an OS image or Supervised/OS install (Umbrel, HA) and a compose-based
  install (Firefly III). Not read in detail: UNVERIFIED beyond the Umbrel landing page.

## Consequences

- The first-run experience depends on ADR 0021's local ingress and the `holdfast` wrapper. Until PA1 lands, the
  installer slices can only deliver "download, verify, generate keys, start the base stack" with a self-signed
  warning. The plan marks the dependency.
- `release.yml` gets heavier: arm64 builds, the bundle, installers, the signature step. It is a protected path
  (Bulwark verdict) and must stay inside the free-tier minute budget (ADR 0013).
- A macOS `.pkg` and a Windows signed `.exe` do not exist until the owner supplies an Apple account and picks a
  Windows signing route. Until then, the one-line stubs and Homebrew are the supported macOS and Windows paths, and
  the docs say "unsigned preview" in the first line.
- Windows and macOS have never run the stack. Expect fixes in compose (volumes, file ownership, line endings,
  Docker Desktop's file sharing). CI cannot prove it (see the plan's matrix), so owner or self-hosted machines are
  part of acceptance.
- Everything depends on `holdfast-finance` going public (SignPath, free arm64 runners, attestations, Homebrew tap
  reach). PLAN 17.6 orders the first release before a one-command installer, so the signed installers wait for it.

## Verification state

Facts read from primary sources on 2026-11-22 carry their URL. **UNVERIFIED** marks anything from memory or not
reachable: Azure Artifact Signing price, winget package IDs and unsigned-installer moderation, Podman and Colima
licences, rootless Podman on ports below 1024, `!override` under podman-compose, Mark-of-the-Web with `irm`, curl
downloads and Gatekeeper, macOS 15's removed bypass, Windows runners' ability to run Linux containers,
Intel-macOS runner virtualization, Homebrew Docker cask names, GitHub attestations on private repos, a Colima VM's
Docker version, and the stack's memory floor for a Raspberry Pi. Each is a spike or an acceptance check in the plan.
