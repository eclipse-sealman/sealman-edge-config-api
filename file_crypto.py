import os
from typing import List

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

ENV_NAME = "FILE_ENCRYPTION_KEY"


class FileEncryptionError(Exception):
    """Raised when file content cannot be encrypted or decrypted. Never carries file content."""


def _keys() -> List[str]:
    return [k.strip() for k in os.getenv(ENV_NAME, "").split(",") if k.strip()]


def _fernet() -> MultiFernet:
    keys = _keys()
    if not keys:
        raise FileEncryptionError(f"{ENV_NAME} is not set")
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except ValueError:
        raise FileEncryptionError(f"{ENV_NAME} is not a valid Fernet key list") from None


def encryption_available() -> bool:
    return bool(_keys())


def encrypt(text: str) -> bytes:
    """Encrypt with the first configured key."""
    return _fernet().encrypt(text.encode("utf-8"))


def decrypt(token: bytes) -> str:
    """Decrypt with any configured key."""
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, TypeError):
        raise FileEncryptionError("Stored file cannot be decrypted") from None
