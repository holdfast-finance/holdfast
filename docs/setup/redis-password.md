# Redis password

Redis holds your login lockouts, job queue and locks, so it always requires a password. New
installs get one from `make keys` (`./scripts/dev.ps1 keys` on Windows), stored as
`REDIS_PASSWORD` in `.env`. The api and worker add it to `REDIS_URL` themselves, so the URL in
`.env` stays `redis://redis:6379/0`.

## Upgrading an existing install

`make up`, `make migrate` and the other targets stop with a one-line error until `.env` has the
value. To add it:

```
make keys-add-missing   # Windows: ./scripts/dev.ps1 keys-add-missing
```

This appends the generated values the file lacks (today `REDIS_PASSWORD`). It never rewrites or
prints an existing value, and it never generates `FERNET_KEY` or the Postgres passwords for an
existing install. Then restart
the stack (`make down && make up`). Redis keeps no data you need across the restart: it holds
queues and locks only, so a restart is safe.

## Dev stack and the host

The dev override still publishes Redis on `127.0.0.1:56379` (loopback only) so host-side
`make test` can reach it. Tests build their URL from `REDIS_PASSWORD` in `.env`. To point them
elsewhere, set `TEST_REDIS_URL` (for example `redis://:<password>@127.0.0.1:56379/15`).
Production never publishes Redis. `make ports` fails if Redis is ever rendered without
`--requirepass`.
