import pytest
from cryptography.fernet import Fernet

import file_crypto
from file_crypto import FileEncryptionError

ENV = "FILE_ENCRYPTION_KEY"


def test_roundtrip_and_rotation(monkeypatch):
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    monkeypatch.setenv(ENV, key_a)
    assert file_crypto.encryption_available()
    old_token = file_crypto.encrypt("x")
    assert isinstance(old_token, bytes)
    assert file_crypto.decrypt(old_token) == "x"

    # rotation: B encrypts, A still decrypts
    monkeypatch.setenv(ENV, f"{key_b},{key_a}")
    assert file_crypto.decrypt(old_token) == "x"
    new_token = file_crypto.encrypt("y")

    # the new token is readable with B alone
    monkeypatch.setenv(ENV, key_b)
    assert file_crypto.decrypt(new_token) == "y"
    # the old token is not, and the error does not leak content
    with pytest.raises(FileEncryptionError):
        file_crypto.decrypt(old_token)


def test_no_key(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert not file_crypto.encryption_available()
    with pytest.raises(FileEncryptionError):
        file_crypto.encrypt("x")
    with pytest.raises(FileEncryptionError):
        file_crypto.decrypt(b"whatever")
