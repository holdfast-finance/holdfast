# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
### Security
- Redis now requires a password (`REDIS_PASSWORD`). The dev stack published it on loopback with no authentication, so any local process could reach it. New installs get the value from `make keys`. Existing installs run `make keys-add-missing`, then restart. See `docs/setup/redis-password.md`.

### Added
- Repository foundation: PLAN v1.1, brand system, ADRs 0001–0006, Claude Code configuration (rules, subagents, skills, hooks), secret-scanning git hook.
