"""Builders for certificate/key/CRL test material, generated with `cryptography`."""

import datetime
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

_KEY = ec.generate_private_key(ec.SECP256R1())


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _cert(subject: str, days: int) -> x509.Certificate:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(_name(subject))
        .public_key(_KEY.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days))
        .sign(_KEY, hashes.SHA256())
    )


def make_cert(subject: str = "plc-line3", days: int = 365) -> bytes:
    return _cert(subject, days).public_bytes(serialization.Encoding.PEM)


def make_cert_der(subject: str = "plc-line3", days: int = 365) -> bytes:
    return _cert(subject, days).public_bytes(serialization.Encoding.DER)


def make_key(password: Optional[bytes] = None) -> bytes:
    encryption = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    return _KEY.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )


def make_crl() -> bytes:
    now = datetime.datetime.now(datetime.timezone.utc)
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(_name("ca"))
        .last_update(now)
        .next_update(now + datetime.timedelta(days=7))
        .sign(_KEY, hashes.SHA256())
    )
    return crl.public_bytes(serialization.Encoding.PEM)


def make_csr() -> bytes:
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(_name("csr"))
        .sign(_KEY, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM)
