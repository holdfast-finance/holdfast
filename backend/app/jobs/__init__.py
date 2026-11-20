"""Job framework (ADR 0016): typed registry, idempotent enqueue, per-household lock, retries.

Modules: ``registry`` (``@job``, payload schemas, retry policy), ``queue`` (arq pool + JSON
serializer), ``lock`` (household advisory lock), ``runner`` (one delivery). Enqueue and the
``job_run`` state transitions live in ``app.services.jobs``. Kept import-light on purpose: no
re-exports, so ``app.services`` can import ``app.jobs.registry`` without pulling in the runner.
"""
