# Instances: `holdfast-instance` (real data) and `holdfast-sandbox` (throwaway)

Three compose projects can run on one machine. They never share a volume, an env file or a port.

| Project | Purpose | State | Env file | Published port |
|---|---|---|---|---|
| `holdfast` (dev, `make up`) | development, hot reload | named volumes, `./data/documents` | `./.env` | `127.0.0.1:443`, db `5432`, redis `56379` (dev override) |
| `holdfast-instance` | **real data**, the only place `PLAID_ENV=production` exists | bind mounts under `$HOLDFAST_INSTANCE_DIR` | `$HOLDFAST_INSTANCE_DIR/instance.env` | caddy `127.0.0.1:9443` only |
| `holdfast-sandbox` | throwaway, synthetic keys | named volumes of its own | `./.env.sandbox` (git-ignored) | caddy `127.0.0.1:9444` only |

Ports are changed with `HOLDFAST_INSTANCE_PORT` and `HOLDFAST_SANDBOX_PORT`. Neither project layers the
dev override, so neither publishes Postgres or Redis (CLAUDE.md rule 12). `make ports` checks both
(`scripts/check-compose-ports.py --instance|--sandbox`, also run in CI).

## Why bind mounts

A named volume can be deleted by `docker compose down -v`, `docker volume rm` and
`docker volume prune`. The instance keeps everything on bind mounts in a directory you choose, so
none of those commands can reach it. `HOLDFAST_INSTANCE_DIR` has no default: no path is baked into
code (rule 13). Pick a directory outside the repository on an encrypted disk. ADR 0019 applies.

```
$HOLDFAST_INSTANCE_DIR/      mode 0700, owned by you
  instance.env               mode 0600: its own keys, never the dev .env
  postgres/ redis/           database and queue state
  documents/                 the document store (owned by uid 10001)
  caddy_data/ caddy_config/  local CA and Caddy state
```

## First run

```bash
export HOLDFAST_INSTANCE_DIR=/path/you/chose        # outside the repo
make instance-init     # creates the directory (0700), subdirectories, instance.env with fresh keys
make instance-up       # checks the directory, builds, starts db and redis, migrates, starts the rest
```

Windows hosts: `./scripts/dev.ps1 instance-init` and `instance-up` (pwsh 7). The check itself is
`scripts/instance.sh`, a bash script, so use WSL or macOS/Linux for the instance host.

`instance-up` (and `-down`, `-migrate`, `-ps`, `-logs`) refuses to run unless the directory:
exists and is absolute, is not a symlink, is owned by you, is **mode 0700**, is outside the
repository, and holds a mode-0600 `instance.env`. It runs before anything touches Docker.

Then open `https://localhost:9443`, enrol a passkey or TOTP, and follow PHASE-2 contract section 12 to
add the production Plaid keys to `instance.env`:

```bash
$EDITOR "$HOLDFAST_INSTANCE_DIR/instance.env"        # PLAID_ENV=production, keys, allowlisted products
docker compose -p holdfast-instance --env-file "$HOLDFAST_INSTANCE_DIR/instance.env" \
  -f docker-compose.yml -f docker-compose.instance.yml up -d --force-recreate api worker
```

## Sandbox

```bash
make sandbox-up        # fresh synthetic keys in .env.sandbox, migrate, start
make sandbox-down      # stop, keep volumes
make sandbox-destroy   # containers, volumes and .env.sandbox
```

Three layers keep the sandbox off real data: the compose file forces `PLAID_ENV=sandbox` and an
empty production secret (a literal beats the env file), it sets `HOLDFAST_SANDBOX=true`, and the api
and worker then **refuse to start** with `PLAID_ENV=production` (`app/config.py`). `make ports`
fails if any of the three disappears.

## Guards for agents

- `.claude/hooks/guard-volumes.sh` binds Claude sessions: it blocks `docker volume rm|prune`,
  `docker system prune --volumes`, and a delete or move (`rm`, `rmdir`, `unlink`, `shred`,
  `truncate`, `mv`, `find -delete`) in any command that names `$HOLDFAST_INSTANCE_DIR`, its
  resolved path, `holdfast-instance` or `instance.env`. It matches the whole command string and
  fails closed; it can't see a path assembled at run time. It's a guard rail, not a sandbox.
- `scripts/fc-delegate.sh` binds Codex and Grok workers: it hides `$HOLDFAST_INSTANCE_DIR`, a
  dedicated engine socket in `$HOLDFAST_INSTANCE_DOCKER_SOCK`, the standard container sockets and
  the whole per-user runtime directory (session bus, systemd user manager, keyring). It fails
  closed: with no `HOLDFAST_INSTANCE_DIR` and no pointer file it refuses to start, unless you set
  `HOLDFAST_INSTANCE_DIR=none` on a machine that hosts no instance. `make instance-init` writes the
  pointer files `${XDG_CONFIG_HOME:-$HOME/.config}/holdfast/instance-dir` (and `instance-docker-sock`
  when that variable is set), mode 0600, which both `fc-delegate.sh` and the hook fall back to.
- The hook also blocks overwriting `instance.env` or files under the directory (redirects, cp, dd,
  tee, install, `sed -i`), and fails closed without `jq` or on unparseable input. Its header lists
  what it cannot catch.
- `make down` in the dev checkout, and `docker compose down -v` in any project, leave
  `holdfast-instance` alone: different project name, bind mounts instead of volumes.

## Moving the dev stack's data into `holdfast-instance` (Keel runs this, once)

Not part of H2's code and not run by it. Do it on the instance host, from your own terminal, with
the dev stack still up and idle. Back up first (`docs/ops/backup.md`).

1. `make instance-init`. Do not run `instance-up` yet: it migrates an empty database.
2. Copy `FERNET_KEY` and `IMPORT_ID_KEY` from the dev `.env` into `instance.env`, by editor, without
   printing them. The stored ciphertext opens only with the same `FERNET_KEY`. Keep the instance's
   own Postgres and Redis passwords, `SESSION_SECRET`, and `APP_BASE_URL`/`WEBAUTHN_ORIGIN`. Passkeys
   are bound to the origin, so re-enrol them on `https://localhost:9443`.
3. Start only the data services:
   `docker compose -p holdfast-instance --env-file "$HOLDFAST_INSTANCE_DIR/instance.env" -f docker-compose.yml -f docker-compose.instance.yml up -d --wait db redis`
4. Dump from the dev database as the owner role, without owners or grants, and restore as the owner
   (the init script's default privileges give `holdfast_app` its access). Run both inside the
   containers so no password reaches argv or the shell history:
   `docker compose -p holdfast exec -T db sh -c 'PGPASSWORD="$POSTGRES_OWNER_PASSWORD" pg_dump -U holdfast_owner -Fc --no-owner --no-privileges holdfast' > "$HOLDFAST_INSTANCE_DIR/restore.dump"`
   then the same file into `pg_restore -U holdfast_owner --no-owner -d holdfast` in the instance's
   `db`. Delete `restore.dump` afterwards (from your own terminal).
5. Copy the document store: `data/documents/.` into `$HOLDFAST_INSTANCE_DIR/documents/`, then
   `chown -R 10001:10001` through a root container as `scripts/instance.sh init` does.
6. Verify by counts only: row counts of `household`, `account`, `transaction` and `import_run`
   match between the dev and instance databases; `alembic_version` is the same revision; a sealed
   value opens (log in and open Settings, or run the key check from `docs/ops/backup.md`).
7. `make instance-up` (migrates to head, starts everything). Check `make instance-ps`, then
   `curl -fsSk https://localhost:9443/api/health`.
8. Only after a successful backup of the instance, `make down` the dev stack. Keep its volumes
   until you have used the instance for a while.
