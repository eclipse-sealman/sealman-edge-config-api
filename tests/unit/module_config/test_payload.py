"""Literal serialization, strict JSON parsing and safe POST errors in module_config/payload.py."""

import json

import pytest
from fastapi import HTTPException

from routers.module_config.payload import ModuleConfigPayload, ModuleJSONResponse, invalid_module_payload, parse_module_json


@pytest.mark.parametrize("payload", [
    {"required": None, "optional": None, "nested": {"key": None}, "items": [None, {"null": None}]},
    [None, {"key": None}], None, True, 12, 1.5, "value",
])
def test_module_config_payload_preserves_literal_json_with_exclude_none(payload):
    model = ModuleConfigPayload(payload)
    assert json.loads(model.model_dump_json(exclude_none=True)) == payload
    assert json.loads(ModuleJSONResponse((model, 200)).body) == [payload, 200]


@pytest.mark.parametrize("raw", [
    b"", b"   ", b"{", b'{"x":}', b"null trailing", b"\xff", b"NaN", b"Infinity", b"-Infinity",
    b'{"nested": [NaN]}', b"1e9999", b"-1e9999", b'{"x":1e9999}',
])
def test_parse_module_json_rejects_empty_malformed_and_non_finite(raw):
    with pytest.raises(HTTPException) as exc:
        parse_module_json(raw)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Request body must be valid finite JSON"


@pytest.mark.parametrize("raw,payload", [
    (b"null", None), (b"[]", []), (b"false", False), (b'{"value":1e2}', {"value": 100.0}),
    (b'{"nested":{"a":null},"items":[null]}', {"nested": {"a": None}, "items": [None]}),
])
def test_parse_module_json_keeps_json_semantics(raw, payload):
    assert parse_module_json(raw) == payload


def test_invalid_module_payload_bounds_locations_and_never_relays_validator_messages():
    response = invalid_module_payload([
        {"loc": ["field" * 100] * 100, "rule": "enum", "msg": "secret password and full schema"}
    ] * 100)
    body = json.loads(response.body)
    assert response.status_code == 422
    assert set(body) == {"message", "errors"}
    assert len(body["errors"]) == 20
    assert len(body["errors"][0]["loc"]) == 16
    assert len(body["errors"][0]["loc"][0]) == 128
    assert body["errors"][0]["rule"] == "enum"
    assert "secret" not in response.body.decode()


def test_parser_recursion_failure_is_a_safe_400(monkeypatch):
    def too_deep(*args, **kwargs):
        raise RecursionError("raw parser exception")

    monkeypatch.setattr(json, "loads", too_deep)
    with pytest.raises(HTTPException) as exc:
        parse_module_json(b"[]")
    assert exc.value.status_code == 400
    assert "raw parser" not in exc.value.detail


def test_unknown_validator_rule_is_safe():
    body = json.loads(invalid_module_payload([{"rule": "secret-custom-rule", "msg": "secret"}]).body)
    assert body["errors"] == [{"loc": [], "rule": "schema", "msg": "must satisfy the declared constraint"}]


@pytest.mark.parametrize("depth", [300, 900])
def test_module_config_payload_round_trips_parser_accepted_deep_json(depth):
    raw = b'{"x":' + b"[" * depth + b'"leaf"' + b"]" * depth + b"}"
    payload = parse_module_json(raw)
    adapter = ModuleConfigPayload(payload)
    assert json.loads(adapter.model_dump_json(exclude_none=True)) == payload


@pytest.mark.parametrize("raw", [
    b'{"x":"\\ud800"}', b'{"\\ud800":null}', b'{"x":["\\udfff",{"\\ud800":"\\ud800"}]}',
])
def test_module_config_payload_escapes_accepted_surrogate_strings_and_keys(raw):
    payload = parse_module_json(raw)
    serialized = ModuleConfigPayload(payload).model_dump_json(exclude_none=True)
    assert json.loads(serialized) == payload
    assert serialized.encode("ascii")


def test_invalid_module_payload_preserves_surrogate_location_in_safe_json():
    response = invalid_module_payload([{"loc": ["\ud800"], "rule": "type", "msg": "secret"}])
    assert response.status_code == 422
    assert json.loads(response.body)["errors"] == [
        {"loc": ["\ud800"], "rule": "type", "msg": "must match the declared type"}
    ]
    assert b"secret" not in response.body


def test_module_config_payload_is_serialized_before_helper_use(monkeypatch):
    from routers.module_config import payload as payload_module

    supplied = {"key": None, "_sa_keep": "\ud800"}
    adapter = ModuleConfigPayload(supplied)

    def no_late_payload_encoding(*args, **kwargs):
        raise AssertionError("helper must receive already-encoded JSON")

    monkeypatch.setattr(payload_module, "_encode_module_json", no_late_payload_encoding)
    assert json.loads(adapter.model_dump_json(exclude_none=True)) == supplied
