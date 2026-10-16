import httpx
from fastapi import FastAPI

from routers.devices import router as devices_router


def _app_with_stubbed_device_dependencies() -> FastAPI:
    app = FastAPI()
    app.include_router(devices_router.devices)

    put_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/devices/{device_id}"
        and "PUT" in getattr(route, "methods", set())
    )
    for dependency in put_route.dependant.dependencies:
        app.dependency_overrides[dependency.call] = lambda: None

    return app


def test_put_device_openapi_includes_json_body_schema():
    app = _app_with_stubbed_device_dependencies()

    operation = app.openapi()["paths"]["/devices/{device_id}"]["put"]
    request_body = operation["requestBody"]
    schema_ref = request_body["content"]["application/json"]["schema"]["$ref"]
    schema_name = schema_ref.rsplit("/", 1)[-1]
    properties = app.openapi()["components"]["schemas"][schema_name]["properties"]

    assert request_body["required"] is True
    assert "meta" in properties
    assert "registration_id_generated" in properties
    assert "deviceType" in properties

    metadata_ref = properties["meta"]["$ref"]
    metadata_name = metadata_ref.rsplit("/", 1)[-1]
    metadata_schema = app.openapi()["components"]["schemas"][metadata_name]
    assert set(metadata_schema["required"]) == {
        "city",
        "countryCode",
        "description",
        "geoLocation",
    }


async def test_put_device_without_body_returns_422():
    app = _app_with_stubbed_device_dependencies()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.put("/devices/eg-123")

    assert response.status_code == 422


async def test_put_device_requires_default_metadata_fields():
    app = _app_with_stubbed_device_dependencies()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.put("/devices/eg-123", json={"meta": {}})

    assert response.status_code == 422
    errors = response.json()["detail"]
    missing_fields = {error["loc"][-1] for error in errors}
    assert missing_fields == {"city", "countryCode", "description", "geoLocation"}


async def test_put_device_passes_json_body_to_create_device(monkeypatch):
    app = _app_with_stubbed_device_dependencies()
    captured = {}

    async def fake_create_device(device_id, body, repo, template_repo):
        captured.update(device_id=device_id, body=body)
        return {"deviceId": device_id}

    monkeypatch.setattr(devices_router, "create_device", fake_create_device)
    request_body = {
        "meta": {
            "city": "Kempten",
            "countryCode": "DE",
            "description": "Example edge device",
            "geoLocation": "47.737315601756144,10.339716700209028",
            "customer": "Example",
        },
        "type_id": "default",
        "registration_id_generated": "reg-123",
        "description": "Example edge device",
        "deviceType": "9",
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.put("/devices/eg-123", json=request_body)

    assert response.status_code == 200
    assert response.json() == {"deviceId": "eg-123"}
    assert captured["device_id"] == "eg-123"
    assert captured["body"]["meta"]["city"] == "Kempten"
    assert captured["body"]["meta"]["countryCode"] == "DE"
    assert captured["body"]["meta"]["customer"] == "Example"
    assert captured["body"]["registration_id_generated"] == "reg-123"
