"""Platform keys and webhook secrets, encrypted at rest with SOPOPS_SECRET_KEY (a Fernet key).

Make one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
Without it, sopops can't store platform credentials (everything else works).
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

ENV = "SOPOPS_SECRET_KEY"


class SecretKeyMissing(Exception):
    pass


def _fernet() -> Fernet:
    key = os.environ.get(ENV, "").strip()
    if not key:
        raise SecretKeyMissing(f"set {ENV} (a Fernet key) to store platform keys and webhook secrets")
    try:
        return Fernet(key.encode())
    except ValueError:
        raise SecretKeyMissing(f"{ENV} isn't a Fernet key (32 url-safe base64-encoded bytes)") from None


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise SecretKeyMissing(f"stored secrets can't be decrypted with this {ENV}; it changed since they were saved") from None


def hint(plain: str) -> str:
    """The first 3 and last 6 characters of a long key (`sk-…a1b2c3`), the last 4 of a shorter one, for telling keys
    apart in the UI."""
    if len(plain) >= 24:
        return plain[:3] + "…" + plain[-6:]
    return "…" + plain[-4:] if len(plain) >= 12 else "…"
