"""API bearer token, kept in the Keychain. watchd creates it; the CLI reads it (same macOS user)."""

from __future__ import annotations

import secrets

import keyring

SERVICE = "cmf-studio"
ACCOUNT = "api-token"


def get_token() -> str | None:
    return keyring.get_password(SERVICE, ACCOUNT)


def ensure_token() -> str:
    token = get_token()
    if not token:
        token = secrets.token_urlsafe(32)
        keyring.set_password(SERVICE, ACCOUNT, token)
    return token
