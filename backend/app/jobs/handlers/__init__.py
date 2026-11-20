"""Job handler modules. Every ``*.py`` here (not starting with ``_``) is imported by
``app.jobs.registry.discover_jobs()`` so its ``@job`` declarations register. Adding a job never
edits a shared file (ADR 0016).
"""
