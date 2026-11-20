# Holdfast release policy

Draft for Keel. The policy covers Holdfast's self-hosted product. The public release milestone follows PLAN §17.6. GitHub repository visibility and product release readiness are tracked separately.

## Versioning

The product uses [SemVer 2.0.0](https://semver.org/). The first product version is `0.0.0-alpha`, with release tag `v0.0.0-alpha`. The web manifest uses that same string. Python packaging cannot use the SemVer hyphen, so the package and lock metadata use the [PEP 440](https://peps.python.org/pep-0440/) form `0.0.0a0`. `0.0.0-alpha` and `0.0.0a0` are one pre-release, not two versions. Update the manifests and affected lock metadata together. Later numbered prereleases map the same way, such as `0.1.0-beta.1` to `0.1.0b1` and `1.0.0-rc.1` to `1.0.0rc1`. Phase tags such as `phase-1.5` mark accepted milestones. They are not product versions.

- Bump the minor version for intentional compatibility breaks during initial development and document the change and migration steps.
- Bump the prerelease identifier for a new artifact on the same version core.
- From `1.0.0`, bump patch for compatible fixes, minor for compatible features or deprecations, and major for incompatible changes to the supported contract.
- Publish each version once. Use a revert PR and a new release for a correction.

## Compatibility and promotion

Before `1.0.0`, each release names the upgrade paths tested for that version and documents breaking changes. The stable compatibility contract identifies the supported API and import/export formats, supported upgrade paths, and configuration or deployment interfaces that callers and operators can rely on. Release notes list changes to the contract, affected users, and required upgrade steps.

| Stage | Evidence required |
| --- | --- |
| Alpha | Package and tag versions agree. Build artifacts and checksums are available. A clean install and first-run smoke pass with synthetic data. |
| Beta | Sounding records Phase 5 PASS. An upgrade from the previous supported version and a backup-and-restore drill pass. Release notes list known limitations and instructions for reporting beta feedback. |
| Release candidate | The supported compatibility contract and upgrade paths are published with the candidate. The team tests a clean install, a supported upgrade and recovery using the actual candidate artifacts. |
| Stable | Keel verifies candidate evidence, resolves release blockers, and confirms readiness under PLAN §17.6. |

## Release verification

1. Harbor verifies that the candidate is a green commit on `main`, both merge gates passed on its PR, and the tag, changelog, manifests, lock metadata and artifact versions agree. During development of the release job, capture a successful dry run and demonstrate that a version mismatch stops publishing.
2. Sounding records the clean install and upgrade checks that apply to the stage. Attach command results and workflow links to the candidate SHA. Use synthetic accounts and data.
3. Beacon drafts release notes with the supported upgrade path, compatibility changes, known limitations and artifact verification instructions. Include backup and recovery instructions for schema changes.
4. Keel alone owns tags and GitHub releases, and is the only one who creates either. Record the tag, commit, and workflow run. Verify that the published checksums match the artifacts that passed acceptance. Setting the version strings in a pull request does not create a tag or a GitHub release.
5. If a release needs correction, Keel uses the PR gate to revert the application change and releases a new version. Follow the documented recovery procedure for schema changes.

## CI

Release jobs stay on GitHub's free tier ([ADR 0013](../adr/0013-free-tier-cicd.md)). There is no paid CI plan. Harbor owns `.github/workflows`. This policy does not change those workflows.
