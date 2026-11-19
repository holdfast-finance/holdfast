"""Domain logic: categorization, transfers, budgets, metrics, forecasts.

Purity rule (ADR 0011): code under ``app.domain`` is pure. It takes plain values (``Decimal``,
dataclasses, Pydantic schemas) and returns plain values. It never does I/O: no database sessions,
ORM models, Redis, HTTP clients, filesystem, clock or environment reads, and no FastAPI. It must not
import ``app.api``, ``app.services``, ``app.db``, ``app.db_context``, ``app.models``,
``app.integrations``, ``app.worker`` or ``app.config``. Services load data, call the domain and
persist the result inside one transaction. ``make lint-imports`` enforces the import half of this.
"""
