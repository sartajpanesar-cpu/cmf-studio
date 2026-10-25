"""Pairing keys in the macOS Keychain (via ``keyring``), never on disk.

Same service/account scheme scripts/phase0_live.sh used, so the key paired in Phase 0 is found.
"""

from __future__ import annotations

from typing import Protocol

import keyring

SERVICE = "cmf-studio"


def _account(address: str) -> str:
    return f"pairing:{address}"


class KeyStore(Protocol):
    def get(self, address: str) -> bytes | None: ...
    def put(self, address: str, key: bytes) -> None: ...
    def delete(self, address: str) -> None: ...


class KeychainKeyStore:
    def get(self, address: str) -> bytes | None:
        value = keyring.get_password(SERVICE, _account(address))
        return bytes.fromhex(value) if value else None

    def put(self, address: str, key: bytes) -> None:
        keyring.set_password(SERVICE, _account(address), key.hex())

    def delete(self, address: str) -> None:
        keyring.delete_password(SERVICE, _account(address))


class MemoryKeyStore:
    """For tests and the simulator."""

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def get(self, address: str) -> bytes | None:
        return self._keys.get(address)

    def put(self, address: str, key: bytes) -> None:
        self._keys[address] = key

    def delete(self, address: str) -> None:
        self._keys.pop(address, None)
