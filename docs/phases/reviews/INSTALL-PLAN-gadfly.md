# Gadfly review: ADR 0037 and INSTALL-PLAN (PR #75, head a7f7190)

Verdict: **REWORK-large.** The shape (one bundle, one `holdfast` core, thin per-OS shells, a verified stub) holds.
What fails is the premise (the repo is already public), the data-safety story (uninstall deletes the key, a backup
before upgrade is optional, Docker Desktop holds the data in a disk the user can't see), and the critical path: the
owner's goal can't be met until ADR 0021's Phase 8.5 work lands, and this plan neither schedules it nor says so.

Ranked. Each item gives the section and the change.

## Blockers

**B1. The plan assumes a private repo. It is public.** `gh repo view holdfast-finance/holdfast` returns
`"visibility":"PUBLIC"`, and CLAUDE.md says "(public)". INSTALL-PLAN "Where this sits" says "private until PLAN 17.6".
Q8, the "public repo" gates on IN4, IN13, IN-W2 and IN14, slice step 7, the whole minute-budget section, and ADR 0037
Consequences ("Everything depends on going public") are all stale. Change: delete Q8. Re-gate IN4 on
`ubuntu-24.04-arm` now. Make SignPath's gate "a released project", which v0.0.0-alpha may already satisfy (ask
SignPath). Replace the budget section with the public-repo facts (standard runners unmetered; concurrency limits
still apply). Then re-derive the slice order (B5).

**B2. Uninstall deletes the secrets that the kept volumes need.** ADR §11 and §5 say uninstall "removes the CLI and
bundle directory, keeping the Docker volumes (the household's data and secrets)". The secrets are not in the volumes.
They are in `.env`: `FERNET_KEY` (the ADR 0015 key ring for every bank token, exchange key and xpub) and the Postgres
role passwords that `pgdata` already holds. §7 puts the install, and so `.env`, under `$XDG_DATA_HOME/holdfast`, and
Windows puts it under `%LOCALAPPDATA%\Programs\Holdfast`, which the uninstaller removes. After that, a reinstall runs
`holdfast keys`, which finds no `.env` and writes fresh secrets. The old `pgdata` then rejects the new passwords, and
every encrypted column is unrecoverable. `scripts/gen-keys.py` already warns about exactly this (line 95). Change: add
a **state directory, separate from code** to §2, §5, §6 and §7: `~/.local/state/holdfast` or the XDG equivalent,
`~/Library/Application Support/Holdfast`, and `%LOCALAPPDATA%\Holdfast` (not `Programs\`). Keep it out of Inno's
`[UninstallDelete]` and out of the brew Cellar or prefix. Uninstall keeps it. `--purge` removes it last. IN15 adds a
test: install, create data, uninstall, reinstall, and the old encrypted row still decrypts.

**B3. The pre-upgrade backup is optional, and there is no rollback.** §11 says "takes a backup if one is
configured". On real financial data that is a data-loss bug. Downgrade is refused, so a failed migration has no way
back. Three things make the path one-way:
- the F5c `hf1` envelope: after a rewrite, an older image can't read the rows (ADR 0015 §5);
- Alembic data migrations;
- a Postgres major bump. `postgres:16-alpine` is pinned at `docker-compose.yml:91`, and a 16→17 image cannot start
  on the old `pgdata` at all. The plan never mentions it.

Change §11 and IN15. `update` always takes a backup first: `pg_dump` plus documents plus a copy of the key ring, to a
**host** path. It verifies the backup by restoring it into a scratch container, and it refuses to proceed if the
backup fails. Before migrating, it stops `api` and `worker`. It keeps the N-1 bundle and digests on disk.
"Rollback" is defined as `holdfast restore <pre-update backup>` on the N-1 digests, never an image downgrade.
Postgres major bumps go through dump and restore inside `update`, with a test. IN15's acceptance adds: a migration
fails halfway, rollback runs, and the data is byte-identical. Also add a release gate: no installer channel (stub,
.exe, tap) is published while README says "no upgrade path before beta". Strangers would install a version they
can't upgrade from (owner question N1).

**B4. On Docker Desktop and Colima the household data lives in a VM disk the user doesn't know exists.** Several
ordinary actions delete every named volume with no Holdfast code involved: Docker Desktop "Clean / Purge data",
"Reset to factory defaults", uninstalling Docker Desktop, `wsl --unregister docker-desktop`, and `colima delete`.
IN2 moves documents from a host bind mount into that VM, which makes this worse. Change §2 and §11: automatic,
scheduled backups to a host directory are **on by default** on Windows and macOS, from first run, before the first
import. The first-run page and `holdfast uninstall` say where the data physically lives. Add a test that the backup
directory is on the host filesystem, not in a volume.

**B5. The owner's goal sits behind Phase 8.5, and the plan doesn't schedule it.** The goal is a stranger on a Mac
or Windows PC who installs and sees a usable app. That needs the local ingress, PA-D2
(`127.0.0.1:47800`, no certificate warning, because Chrome blocks WebAuthn on cert errors and auth is passkey-first).
It also needs the wrapper and installer, PA-D6. ADR 0021 lists both under "Deferred to Phase 8.5". This plan calls
itself "preemptible", names "PA1" (which is not PA-D2), and builds IN7, IN10 and IN15 over the same ground as PA-D6,
with Harbor owning both. Change:
- Either pull PA-D2 and PA-D6 into this lane and mark PA-D6 superseded by IN7, IN10 and IN15 in both documents, or
  state plainly that Mac and Windows strangers get a usable app only after Phase 8.5.
- Reorder so the first proof is cheap and early. New step 1 is an owner-hardware spike: the current release compose
  on one Mac and one Windows PC with Docker Desktop. It checks uid 10001, read-only root, file sharing and
  `--wait`. It runs before IN7–IN9 and before anyone builds an installer. Today that proof is step 6.
- Move IN-L-S1 (Podman) and IN8's long tail of distros below the Mac and Windows path.

**B6. Q3 proposes a self-hosted runner on the owner's machines. The repo is public, and this laptop holds real
statements.** Memory note: this laptop is the long-term host, with real PDFs in `~/.local/share/holdfast-private`.
On a public repo, a self-hosted runner executes PR-controlled code. ADR 0013 §5 already requires same-repo branches
only, its own ADR, and Bulwark first. Change Q3 and the CI matrix: never on the owner's data machine. A dedicated
machine or an ephemeral VM, with no fork triggers, its own ADR, and a Bulwark verdict before it is registered.

## Should-fix

**S1. §8(a) and §4: the stub's hash on "another origin" is not independent.** Cloudflare is the registrar and DNS for
both `get.` and the apex. Whoever holds the account can repoint the apex GitHub Pages site and get a certificate for
it. Under `curl | sh`, nobody compares a hash anyway. Change:
- Say it plainly: the Cloudflare account is the code-execution root for every one-liner install.
- Offer a Cloudflare-free one-liner of equal standing,
  `https://github.com/holdfast-finance/holdfast/releases/latest/download/install.sh`, and print the stub hash on
  github.com, not on our domain.
- Add DNSSEC and CAA records, pinned to the CA in use, to Q7.
- Wrap the stub body in a function that is called on the last line, so a truncated stream runs nothing.

**S2. §8 and §9: the signing identity is "any `v*` tag", and agents create tags.** With full autonomy, any session
with push rights can tag a commit, including one that changes `release.yml`, and Fulcio will certify it. Change:
- a tag ruleset limiting who can create `v*`;
- `release.yml` refuses unless the tagged commit is reachable from `main` and passed `ci-required`;
- the signing job runs in a protected environment;
- §8 states the residual risk.

**S3. §8.5: the freeze attack on fresh installs.** `latest.json` is served from Cloudflare, which is untrusted, and
can point a first-time install at an old, validly signed, vulnerable release. The downgrade guard only protects an
existing install. Change: the stub embeds a minimum version that is raised on security releases, and `latest.json`'s
version must appear in a signed per-release `CHANNEL` asset.

**S4. §9 and the `notarize` job: Apple and SignPath credentials in Actions secrets contradict §9's own argument.**
§9 says an Actions secret is "the very thing a CI compromise steals". Change: put the Developer ID `.p12` and notary
credentials behind a protected environment used only by tag jobs. Record that this re-introduces a stealable key.

**S5. §3 and §5: Docker Desktop for someone who has never heard of it.** The plan's step counts skip all of this:
- Virtualization is off in the firmware on many consumer PCs. WSL then fails with a hex error code the user can't fix.
- Docker Desktop's first launch needs a GUI terms click, so `winget install` followed by `holdfast up` fails headless.
- `winget` is absent or stale on some Windows 10 installs.
- Docker Desktop and Colima are often not running after a reboot (Colima needs `brew services`). The bookmarked
  `localhost:47800` then just refuses to connect.
- Windows has no launcher at all, only macOS gets `Holdfast.app`.
- There are no RAM or disk floors for Windows or Mac. Docker Desktop's VM and the images cost several GB (measure).
- AV and SmartScreen may flag an unsigned Inno installer that downloads and runs scripts.

Change: add a preflight to IN10 and IN11. It checks firmware virtualization (`Win32_Processor`), build, edition,
RAM, disk and winget, and it prints, before any download, what will be needed. Add a `holdfast open` launcher on
every OS (a Start-menu shortcut and `Holdfast.app`). It starts the runtime if it is stopped, waits for the engine,
runs `up`, then opens the browser. Add the first-launch GUI step to the documented step count.

**S6. §5 and Q4, rule 10.** If amended:
- The 5.1 stub must force TLS 1.2 (`[Net.ServicePointManager]`).
- It must handle a missing winget by falling back to the PowerShell MSI from the GitHub release, hash-pinned.
- A CI job must run the stub under `powershell.exe` 5.1 on `windows-2025`. IN10 has no 5.1 job.
- Fix the size mismatch: 60 lines in §5, 80 in IN10.
- Scope the amendment text to "one bootstrap file, `installer/install.ps1`, with a test that it does nothing but get
  pwsh and hand off".

The paranoid path on Windows: a bundle downloaded in a browser carries Mark-of-the-Web, so `RemoteSigned` blocks
`holdfast.ps1`. Document `Unblock-File` after verifying, and test it.

**S7. §2 and IN3: secrets generation per platform is unspecified.** "umask 077 inside the container" does not set the
host file's mode. A container writing through a bind mount creates a file owned by uid 10001 on Linux. Mode 0600
means nothing on NTFS. Change: the container writes to stdout, and the host shim creates the file:
- POSIX: `umask 077`;
- Windows: an ACL of the current user only, set with `icacls` and asserted in Pester.
After writing, the shim shows a one-time "recovery kit" step for the key ring (owner question N2).

**S8. CI matrix: what it really proves.**
- `install-linux-floor`: the runners ship Moby, not docker-ce, so `--install-docker` there tests a package conflict,
  not a user's machine. Run it in a clean `ubuntu:24.04` VM or a Docker-in-VM job, or drop the claim.
- `IN-W-S1`: assume "no". Hosted Windows runners don't run WSL 2 or Linux containers in practice, so plan the
  owner-hardware path now.
- `install-macos-intel`: Intel macOS runner images are being retired. Check the retirement date before building on
  them.
- `ubuntu-26.04`: confirm it is a GA label, not a preview.

**S9. §5: the Inno Setup licence.** "Commercial users are asked to buy a licence." Holdfast Cloud is a revenue line.
Quote the licence text verbatim and say whether the publishing entity counts as commercial, or record the risk under
ADR 0013.

**S10. §5, SignPath "dual-licensing".** Gadfly's ruling: AGPL hosting is not a second licence. But this is SignPath's
call, not ours. Ask it in the application, and state that no Cloud-only proprietary code ships in the signed
artifacts.

**S11. §4 says `/install.sh` is "already deployed" on the apex.** No such file is in this repo or the ops doc. If it
is live, it is unverified code on a public URL today. Inventory it, and take it down or replace it before IN-E1.

**S12. §7 and §6: PATH.** `~/.local/bin` is not on PATH on stock macOS (zsh) and some distros. The stub must say so,
or offer the shell rc line with consent.

## Owner questions missing from the plan

- **N1.** Do we publish any one-liner, .exe or tap before an upgrade path exists (beta)? (B3)
- **N2.** Key-ring escrow: where does a non-technical user keep `FERNET_KEY`, and do backups include it? Including it
  defeats encryption at rest for the backup. Excluding it makes the backup useless without the kit. (B2, S7)
- **N3.** Who is the self-host `.exe` for? BRAND frames self-host as builder-to-builder. Should a non-technical user
  who has never heard of Docker be pointed at Holdfast Cloud instead?
- **N4.** Support channel: install failures from strangers will land as public issues with logs. That needs issue
  templates and a redaction warning before launch.
- **N5.** Who may create `v*` tags (S2)?
