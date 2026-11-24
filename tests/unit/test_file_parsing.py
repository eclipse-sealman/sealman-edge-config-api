import datetime
import hashlib

import pytest

from file_parsing import parse_upload
from tests.pem_fixtures import make_cert, make_cert_der, make_crl, make_csr, make_key

UNSUPPORTED = "Expected a PEM certificate, private key or CRL, or a DER certificate"


def test_kinds():
    parsed = parse_upload(make_cert("plc-line3", days=10))
    assert parsed.kind == "certificate"
    assert "plc-line3" in parsed.subject
    assert parsed.not_after.tzinfo is not None
    assert parsed.not_after.utcoffset() == datetime.timedelta(0)
    remaining = parsed.not_after - datetime.datetime.now(datetime.timezone.utc)
    assert datetime.timedelta(days=9) < remaining <= datetime.timedelta(days=10)

    der = parse_upload(make_cert_der())
    assert der.kind == "certificate"
    assert der.pem.startswith("-----BEGIN CERTIFICATE-----")

    chain = parse_upload(make_cert("a", days=30) + make_cert("b", days=5))
    assert chain.kind == "certificate"
    assert chain.pem.count("-----BEGIN CERTIFICATE-----") == 2
    assert chain.not_after - datetime.datetime.now(datetime.timezone.utc) < datetime.timedelta(days=5)

    key = parse_upload(make_key())
    assert (key.kind, key.subject, key.not_after) == ("private-key", None, None)

    assert parse_upload(make_crl()).kind == "crl"
    assert parse_upload(make_cert() + make_key()).kind == "private-key"


def test_normalizes_text():
    clean = make_cert().decode()
    dirty = b"\xef\xbb\xbf\r\n" + clean.replace("\n", "\r\n").encode() + b"\r\n\r\n"
    parsed = parse_upload(dirty)
    assert "\r" not in parsed.pem
    assert "﻿" not in parsed.pem
    assert parsed.pem.endswith("\n") and not parsed.pem.endswith("\n\n")
    assert parsed.sha256 == hashlib.sha256(clean.encode()).hexdigest()


@pytest.mark.parametrize(
    "raw",
    [b"hello", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, b"\x30\x82\x0a\x00" + b"\x01" * 64, make_csr()],
)
def test_unsupported_content(raw):
    with pytest.raises(ValueError, match=UNSUPPORTED):
        parse_upload(raw)


def test_password_protected_key_refused():
    with pytest.raises(ValueError, match="Password-protected keys are not supported"):
        parse_upload(make_key(password=b"x"))
