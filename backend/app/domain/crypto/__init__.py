"""Pure crypto domain (ADR 0020): public-key validation and address derivation, no I/O.

Hard rule 11: nothing here accepts, stores or returns a seed phrase or private key. Every parser
runs :func:`app.domain.crypto.guard.reject_secret_material` first.
"""
