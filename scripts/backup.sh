#!/usr/bin/env bash
# Holdfast backup: encrypted pg_dump + documents -> restic (PLAN 12). Lands in Phase 9.
# TODO(phase-9): pg_dump via `docker compose exec db`, tar data/documents, restic backup to the
# offsite repo from .env, prune, and a monthly restore test (scripts/restore.sh).
set -euo pipefail
echo "backup.sh: not implemented yet (Phase 9). Nothing was backed up." >&2
exit 1
