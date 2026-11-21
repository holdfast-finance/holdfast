# 0018. Release artifacts on GitHub Releases, PyPI (`holdfast-finance`), GHCR and a Docker Hub mirror

- Status: Accepted
- Date: 2026-11-20
- Deciders: owner (request 2026-11-20: publish the first pre-release to GitHub, PyPI and Docker); Lattice (architect); Keel

## Context
The owner asked for `v0.0.0-alpha` to go out on GitHub, PyPI and Docker. PyPI, GHCR and Docker Hub are
external services, so hard rule 9 needs this ADR before anything is uploaded. (0012 is reserved by PR #8
and 0017 by PR #28, so this is 0018.)

What already constrains the design:
- `docs/process/RELEASES.md`: an annotated `v<semver>` tag plus a GitHub Release on the same `main`
  commit. Pre-releases pass `--prerelease --latest=false`. Each version is published once, and a
  correction is a new version. Python metadata uses the PEP 440 form (`0.0.0-alpha` is `0.0.0a0`).
  Only Keel creates tags and Releases.
- PLAN 19.1 names "GitHub Releases + container images on GHCR, with signed images (Sigstore/cosign) and
  checksums". PLAN 19.2 puts release integrity on Sigstore and checksums, not a blockchain.
- ADR 0013: no paid plan, service or licence for CI/CD.
- ADR 0005: the code is AGPL-3.0-only. Every dependency must be AGPL-compatible.
- PyPI `holdfast` belongs to an unrelated project. Checked 2026-11-20: `pypi.org/pypi/holdfast/json`
  returned 200 and `pypi.org/pypi/holdfast-finance/json` returned 404. `backend/pyproject.toml` already
  declares `name = "holdfast-finance"`, `license = "AGPL-3.0-only"`, and a wheel whose only top-level
  import package is `app`, with Alembic migrations bundled under `app/migrations`.

### Conflict with PLAN 17.6, and which wins
PLAN 17.6 orders the milestones like this: (1) run Holdfast on the owner's own finances for a full tax
year, then (2) clean the repo, write docs, publish under AGPL and add a one-command installer.
`RELEASES.md` follows that order ("The release becomes public when the repository does (PLAN §17.6)").

Publishing to any of these registries is milestone 2, whatever the repo's visibility. The sdist and
wheel are Python source. The API image contains the same source. PyPI has no private projects. A
self-hoster can't pull a private GHCR or Docker Hub image. So once the first upload lands, the code is
public, about a year ahead of the PLAN's timing.

**The owner's request wins on timing.** It is newer and explicit, and the owner decides the project's
publication date (ADR 0013 already listed going public early as an owner option). **PLAN 17.6 still
wins on preconditions.** The conditions of milestone 2 become release gates for the first upload:
nothing personal in the tree or the artifacts (hard rule 13), install docs, and the compose install as
the one-command path. Milestone 1 is not dropped. It moves from "before publishing" to "before the
first stable (`1.0.0`)". No other PLAN section, including Section 20, says anything about distribution.

## Decision
1. **GitHub Releases are the source of truth.** Each version is the annotated tag and a GitHub
   Release (RELEASES.md), with the sdist, the wheel and a `SHA256SUMS` file attached. The release notes
   list the digest of every image. A registry copy that doesn't match `SHA256SUMS` or the listed
   digest is wrong by definition.
2. **One build, many copies.** A workflow triggered by a `v*` tag push first checks that the tag is
   annotated, points at a commit on `main`, and matches the manifest and lock versions. It then builds
   each artifact once in CI from a clean checkout. Upload from a developer machine is never allowed,
   because owner data sits in `data/` on that machine. The workflow publishes the bytes it built:
   - **PyPI** gets the distribution `holdfast-finance`, the same sdist and wheel listed in
     `SHA256SUMS`, through **trusted publishing (OIDC)**. The PyPI publisher is bound to
     `holdfast-finance/holdfast`, the release workflow file, and the `pypi` environment if the plan
     allows environments. Only the publish job has `id-token: write`. No PyPI token is stored anywhere.
     The import package name stays as it is.
   - **GHCR is the primary image registry:** `ghcr.io/holdfast-finance/holdfast-api`, `-web` and
     `-caddy`. The job pushes with the workflow's `GITHUB_TOKEN` (`packages: write`), so no extra
     account is needed.
   - **Docker Hub is a mirror** under a namespace the owner supplies as the repository variable
     `DOCKERHUB_NAMESPACE`. The job copies by digest from GHCR (`buildx imagetools create` or
     equivalent) and does not rebuild, so the two registries serve identical digests. The credentials are
     a Docker Hub access token scoped to push on those repositories, held only as GitHub Actions secrets
     (`DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`). If the variable is unset, the mirror step is skipped. A
     failed mirror step doesn't fail the release and can be re-run.
3. **Tags.** Images are tagged with the product SemVer (`0.0.0-alpha`) and described by digest. **A
   pre-release never moves `latest`** in either registry, and pre-releases get no floating tag (`alpha`,
   `edge`). Only a stable release moves `latest`. PyPI has no `latest` pointer to protect. Pre-release
   handling there belongs to the installer (see Consequences).
4. **Metadata.** The wheel and sdist carry `License-Expression: AGPL-3.0-only` and the `LICENSE` file.
   Images carry the OCI labels `org.opencontainers.image.licenses=AGPL-3.0-only`,
   `org.opencontainers.image.source=https://github.com/holdfast-finance/holdfast`, `.version` and
   `.revision` (the commit SHA).
5. **Artifacts only.** These registries receive build artifacts and their metadata. They never
   receive household or financial data, fixtures derived from real documents, `.env` contents,
   secrets or telemetry. The running app never contacts them. There is no update check and no download
   ping. Before upload, the workflow scans the unpacked sdist, the wheel and each image filesystem with
   gitleaks and fails on a finding.
6. **Signing.** The PyPI publish action's default PEP 740 attestations and keyless cosign signatures
   on the GHCR digests use Sigstore's public-good service. This ADR covers Sigstore under hard rule 9.
   Alpha evidence is `SHA256SUMS` and digests (RELEASES.md). Signed images (PLAN 19.1) are required
   before the first beta.
7. **Ownership.** Harbor owns the workflow, and its changes are a protected path that needs Bulwark's
   verdict. Keel alone pushes the tag that triggers it. The owner-queue items are the PyPI account
   (2FA) and the pending trusted publisher, the Docker Hub account, namespace and scoped token, and
   setting the three GHCR packages public.

## Alternatives considered
- **Claim `holdfast` on PyPI via PEP 541.** The project is unrelated and owned by someone else, and
  PEP 541 doesn't hand over a project that is in use. Rejected.
- **Another distribution name** (`holdfast-app`, `holdfastfinance`). `holdfast-finance` matches the
  GitHub org and the `holdfastfinance.app` domain, so a user can check it against both.
- **Long-lived PyPI API token or Docker Hub password in secrets.** A token can leak and has to be
  rotated by hand. OIDC removes the PyPI token. Docker Hub has no OIDC push for this setup, so a scoped
  token is the minimum. Rejected.
- **Docker Hub as primary.** It needs a separate account and has pull limits. GHCR is tied to the org
  identity already in the image source label. Rejected as primary and kept as a mirror, because many
  self-host templates (Umbrel, Unraid, CasaOS, PLAN 19.1) look there first.
- **GHCR only.** This is simpler, but the owner asked for Docker. The mirror costs one copy step.
- **Rebuild per registry.** The digests would differ and break the single-source-of-truth rule.
  Rejected.
- **No PyPI wheel, compose only.** Compose is the supported install anyway. Not publishing is the
  smaller design, but the owner asked for PyPI, and the name has value (see below). The wheel ships,
  scoped as described in Consequences.
- **Defensively registering near-miss names** (`holdfast-finanse`, `holdfastfinance`). PEP 541 treats
  empty placeholder projects as name squatting. Rejected.
- **Quay.io or a self-hosted registry.** It adds another service and offers nothing over GHCR. Rejected.
- **Floating `alpha` or `edge` image tags.** Nothing would pin them, and they invite people to run
  pre-releases unpinned. Rejected for now.

## Consequences
- **PyPI is permanent.** A file name and version can be uploaded only once. Deleting or yanking a
  release never frees its version number. A yanked version can still be installed with an exact
  `==` pin. Once uploaded, `0.0.0a0` is used up for good. A broken alpha is fixed with
  `0.0.0-alpha.1`, which is `0.0.0a1`. This matches RELEASES.md's "publish each version once". Never
  delete the *project*: that frees the name for anyone to take.
- **Pre-releases on PyPI.** Plain `pip install holdfast-finance` won't pick a pre-release without
  `--pre` or an exact pin. uv's default does select a pre-release when only pre-releases exist. Until a
  stable release exists, "latest on PyPI" effectively means the alpha for uv users. Docs always show an
  exact pin.
- **What the wheel is good for.** It is the API and worker as an exact, hashed artifact. Distro, Nix
  and appliance packagers can build on it, plugin authors can depend on it (the PLAN 20.3 entry-point
  hooks), and it holds the name. **It is not a supported install for alpha.** A wheel alone has no
  Postgres role split (ADR 0007), no Redis, no web UI and no Caddy bound to the Tailscale IP (hard
  rule 12). A user who runs `uvicorn` from it could expose the API on a public interface. The supported
  path stays the compose install, and the release notes say so. Two packaging debts follow:
  - The wheel's top-level import package is the generic name `app`. In a shared environment it can
    collide with any other distribution, or the user's own code, that installs `app/`. Use a dedicated
    venv only. Renaming the import package gets its own ADR before plugins depend on it (before beta).
  - The API image runs from source, not from the wheel, so the compose smoke never runs the wheel. Alpha
    evidence must add a wheel smoke in CI: a fresh 3.12 venv, `pip install` of the built file, the
    `app` import, and `alembic upgrade head` from the bundled `app/alembic.ini` against a throwaway
    Postgres.
- **Licence.** PyPI, GHCR and Docker Hub all accept AGPL-3.0-only content, and AGPL allows verbatim
  copies to be conveyed, so mirrors and caches are fine. Shipping object code (the minified web bundle,
  the images) brings AGPL §6's Corresponding Source duty. It is met by the public tagged repo that the
  OCI `source` and `revision` labels point to, which is one more reason the repo must be public when the
  first image is. Third-party components in the images (Debian base packages, CPython, Caddy under
  Apache-2.0, npm dependencies compiled into the bundle) keep their own licences. The base images rely
  on upstream distro source availability, which is normal practice. The release notes record the base
  image digests. This is not legal advice. The PLAN 17.3 lawyer is for the hosted tier.
- **Name confusion and typosquats.** `pip install holdfast` installs a stranger's project. Every doc,
  README and release note uses `holdfast-finance` in full and never the short form. Typosquats of
  `holdfast-finance` can appear at any time. The defences are PyPI's own malware reporting, an exact pin
  in docs, and verifying against `SHA256SUMS`. For images, docs and compose use fully qualified
  `ghcr.io/holdfast-finance/...` names, pinned by digest in release notes. The unqualified Docker Hub
  name `holdfast/...` may belong to someone else and is never shown.
- **Docker Hub free tier** (check current terms before relying on it): unauthenticated pulls are
  rate-limited per IP, at about 10 per hour at the time of writing, and authenticated free accounts get
  more. Inactive-image retention for free accounts has been announced before and may apply. On current
  plans an organisation namespace is paid, so under ADR 0013 the mirror probably lives under a personal
  namespace. That ties it to one person's account, so it has a bus factor. The Docker-Sponsored Open
  Source programme would lift the limits, and applying is free, so it's a later owner option. Because
  GHCR is primary, a Docker Hub limit or purge loses no artifact.
- **GHCR free tier.** Public packages are free to store and pull. Private packages count against the
  GitHub Free storage and transfer quotas, and three images would exceed those quickly. So the
  packages must be public, which comes back to the PLAN 17.6 conflict above.
- **Privacy for users.** A registry sees the IP address of whoever pulls. PyPI publishes aggregate
  download counts. No financial data is involved in either, and the install docs say so plainly.
- **Follow-ups.** Beacon updates RELEASES.md line 28 and the alpha evidence row: a registry upload is
  public whatever the repo's visibility, and the wheel smoke is added. Harbor: the release workflow,
  the artifact gitleaks scan, OCI labels, the digest-copy mirror step, and the wheel smoke (Bulwark
  reviews). Before beta: cosign signing, the import-package rename ADR, and moving PLAN 17.6
  milestone 1 to "before 1.0.0" in the PLAN's next amendment.
