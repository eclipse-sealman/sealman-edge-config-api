import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Optional

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, load_pem_private_key

UNSUPPORTED_MESSAGE = "Expected a PEM certificate, private key or CRL, or a DER certificate"
ENCRYPTED_KEY_MESSAGE = "Password-protected keys are not supported"

_BLOCK_RE = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----.*?-----END \1-----", re.DOTALL)
_KEY_LABELS = {"PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY"}
_ACCEPTED_LABELS = _KEY_LABELS | {"CERTIFICATE", "X509 CRL"}


@dataclass(frozen=True)
class ParsedFile:
    pem: str
    kind: Literal["certificate", "private-key", "crl"]
    subject: Optional[str]
    not_after: Optional[datetime]
    sha256: str


def _parse_blocks(text: str) -> List[tuple]:
    """Return (label, block_text) for every PEM block, in order."""
    return [(m.group(1), m.group(0).strip()) for m in _BLOCK_RE.finditer(text)]


def parse_upload(raw: bytes) -> ParsedFile:
    """Parse an uploaded PEM/DER file into normalized PEM text and metadata.

    Raises ValueError with a user message. Messages never include file content.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = _parse_blocks(text)

    if not blocks:
        try:
            cert = x509.load_der_x509_certificate(raw)
        except ValueError:
            raise ValueError(UNSUPPORTED_MESSAGE) from None
        pem = cert.public_bytes(Encoding.PEM).decode("ascii")
        return _certificate_result(pem, [cert])

    certs: List[x509.Certificate] = []
    has_key = False
    has_crl = False
    for label, block in blocks:
        if label == "ENCRYPTED PRIVATE KEY" or "Proc-Type: 4,ENCRYPTED" in block:
            raise ValueError(ENCRYPTED_KEY_MESSAGE)
        if label not in _ACCEPTED_LABELS:
            raise ValueError(UNSUPPORTED_MESSAGE)
        data = block.encode("ascii", errors="ignore")
        try:
            if label == "CERTIFICATE":
                certs.append(x509.load_pem_x509_certificate(data))
            elif label == "X509 CRL":
                x509.load_pem_x509_crl(data)
                has_crl = True
            else:
                load_pem_private_key(data, password=None)
                has_key = True
        except (ValueError, TypeError):
            raise ValueError(UNSUPPORTED_MESSAGE) from None

    pem = "\n".join(block for _, block in blocks) + "\n"
    if has_key:
        return ParsedFile(pem, "private-key", None, None, _sha(pem))
    if has_crl:
        return ParsedFile(pem, "crl", None, None, _sha(pem))
    return _certificate_result(pem, certs)


def _sha(pem: str) -> str:
    return hashlib.sha256(pem.encode("utf-8")).hexdigest()


def _certificate_result(pem: str, certs: List[x509.Certificate]) -> ParsedFile:
    first = certs[0]
    return ParsedFile(
        pem=pem,
        kind="certificate",
        subject=first.subject.rfc4514_string(),
        not_after=min(c.not_valid_after_utc for c in certs),
        sha256=_sha(pem),
    )
