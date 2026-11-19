"""Application services: transactions, auth, audit and every write (ADR 0011).

A service owns the unit of work: it opens the scoped session, loads ORM models, calls pure
``app.domain`` logic, persists the result and writes the audit row in one transaction. Money
writes live here, never in a router. Services may import ``app.domain`` but never ``app.api``.
"""
