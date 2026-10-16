import pytest

from extensions import body_validation


def test_check_schema_accepts_valid_schema():
    body_validation.check_schema({"type": "object", "properties": {"a": {"type": "string"}}})


def test_check_schema_rejects_invalid_schema():
    from jsonschema.exceptions import SchemaError

    with pytest.raises(SchemaError):
        body_validation.check_schema({"type": "not-a-real-type"})


def test_validate_once_reports_no_errors_for_conforming_payload():
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    assert body_validation.validate_once(schema, {"name": "abc"}) == []


def test_validate_once_reports_missing_required_field():
    schema = {"type": "object", "required": ["name"]}
    errors = body_validation.validate_once(schema, {})
    assert len(errors) == 1
    assert errors[0]["rule"] == "required"
    assert "name" in errors[0]["msg"]


def test_validate_payload_reports_every_failing_field_not_just_the_first():
    schema = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
    }
    errors = body_validation.validate_payload("route-multi-error", schema, {"a": 1, "b": "x"})
    rules = {e["rule"] for e in errors}
    assert "type" in rules
    assert len(errors) == 2


def test_validate_payload_truncates_oversized_enum_value_list():
    schema = {"enum": list(range(20))}
    errors = body_validation.validate_payload("route-enum", schema, 999)
    assert len(errors) == 1
    assert "...(truncated)" in errors[0]["msg"]


def test_get_validator_is_cached_and_reused_for_same_route_id():
    schema = {"type": "object"}
    first = body_validation.get_validator("route-cache-test", schema)
    second = body_validation.get_validator("route-cache-test", schema)
    assert first is second


def test_invalidate_drops_cached_validator():
    schema = {"type": "object"}
    first = body_validation.get_validator("route-invalidate-test", schema)
    body_validation.invalidate("route-invalidate-test")
    second = body_validation.get_validator("route-invalidate-test", schema)
    assert first is not second


def test_external_ref_fails_closed_never_makes_a_network_call():
    """No `retrieve` callback is configured on the shared Registry, so an external $ref
    raises instead of fetching it over the network — an SSRF-shaped risk closed by
    construction, not convention."""
    schema = {"$ref": "https://example.invalid/should-never-be-fetched.json"}
    with pytest.raises(Exception):
        body_validation.validate_once(schema, {})


def _request_with_body(body: bytes, headers=None):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "headers": headers or [],
        "path": "/x",
        "query_string": b"",
    }
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_parse_json_returns_none_for_empty_body():
    assert body_validation.parse_json(b"") is None


def test_parse_json_rejects_invalid_json():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        body_validation.parse_json(b"{not json")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_read_limited_body_returns_body_within_limit():
    assert await body_validation.read_limited_body(_request_with_body(b"12345"), limit=5) == b"12345"


@pytest.mark.asyncio
async def test_read_limited_body_rejects_oversized_streamed_body():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await body_validation.read_limited_body(_request_with_body(b"123456"), limit=5)
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_read_limited_body_rejects_oversized_content_length_before_reading():
    from fastapi import HTTPException

    request = _request_with_body(b"", headers=[(b"content-length", b"999")])
    with pytest.raises(HTTPException) as exc_info:
        await body_validation.read_limited_body(request, limit=5)
    assert exc_info.value.status_code == 413
