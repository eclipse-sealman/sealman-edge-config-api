"""Platform branding: public reads, permission-guarded writes, strict validation of variables and images."""
import pytest

from routers.branding.service import branding_cache

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ICO = b"\x00\x00\x01\x00" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture(autouse=True)
def _clear_branding_cache():
    branding_cache.clear()
    yield
    branding_cache.clear()


@pytest.fixture
async def public_client(client, db_session):
    """The public sub-app has its own dependency overrides; route it to the per-test session."""
    from db.session import get_db
    from routers.branding.public import public_app

    async def _override_get_db():
        yield db_session

    public_app.dependency_overrides[get_db] = _override_get_db
    yield client
    public_app.dependency_overrides.clear()


class TestPublicRead:
    async def test_defaults_without_saved_branding(self, public_client):
        response = await public_client.get("/public/branding")
        assert response.status_code == 200
        assert response.json() == {
            "theme_id": "default",
            "variables": {},
            "app_title": "",
            "logo_version": None,
            "favicon_version": None,
        }

    async def test_does_not_require_a_token(self, public_client):
        from main import app
        from auth import validate_jwt

        app.dependency_overrides.pop(validate_jwt)
        assert (await public_client.get("/public/branding")).status_code == 200
        assert (await public_client.put("/platform/branding", json={"theme_id": "slate"})).status_code == 401

    async def test_missing_asset_is_404(self, public_client):
        assert (await public_client.get("/public/branding/logo")).status_code == 404


class TestUpdate:
    async def test_saved_branding_is_public(self, public_client):
        body = {"theme_id": "custom", "variables": {"--primary": "24 90% 45%", "--radius": "0.75rem"}, "app_title": " ACME "}
        saved = await public_client.put("/platform/branding", json=body)
        assert saved.status_code == 200, saved.text
        public = (await public_client.get("/public/branding")).json()
        assert public["theme_id"] == "custom"
        assert public["variables"] == {"--primary": "24 90% 45%", "--radius": "0.75rem"}
        assert public["app_title"] == "ACME"

    @pytest.mark.parametrize(
        "variables",
        [
            {"--unknown": "0 0% 0%"},
            {"--primary": "red"},
            {"--primary": "0 0% 0%; } body { background: url(https://evil)"},
            {"--radius": "calc(1rem)"},
        ],
    )
    async def test_rejects_anything_but_known_variables_with_plain_values(self, public_client, variables):
        response = await public_client.put("/platform/branding", json={"theme_id": "custom", "variables": variables})
        assert response.status_code == 422

    async def test_rejects_invalid_theme_id(self, public_client):
        response = await public_client.put("/platform/branding", json={"theme_id": "<script>"})
        assert response.status_code == 422


class TestAssets:
    async def test_upload_and_serve_logo(self, public_client):
        uploaded = await public_client.put("/platform/branding/logo", content=PNG)
        assert uploaded.status_code == 200, uploaded.text
        version = uploaded.json()["logo_version"]
        assert version

        response = await public_client.get(f"/public/branding/logo?v={version}")
        assert response.status_code == 200
        assert response.content == PNG
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in response.headers["content-security-policy"]
        assert "immutable" in response.headers["cache-control"]

        not_modified = await public_client.get(
            "/public/branding/logo", headers={"If-None-Match": response.headers["etag"]}
        )
        assert not_modified.status_code == 304

    async def test_svg_is_served_sandboxed(self, public_client):
        assert (await public_client.put("/platform/branding/logo", content=SVG)).status_code == 200
        response = await public_client.get("/public/branding/logo")
        assert response.headers["content-type"] == "image/svg+xml"
        assert "sandbox" in response.headers["content-security-policy"]

    async def test_type_is_detected_from_content_not_header(self, public_client):
        response = await public_client.put(
            "/platform/branding/logo", content=b"<html><script>x</script></html>", headers={"Content-Type": "image/png"}
        )
        assert response.status_code == 415

    async def test_favicon_types(self, public_client):
        assert (await public_client.put("/platform/branding/favicon", content=ICO)).status_code == 200
        assert (await public_client.put("/platform/branding/favicon", content=JPEG)).status_code == 415

    async def test_size_limit(self, public_client):
        too_big = PNG + b"\x00" * (128 * 1024)
        assert (await public_client.put("/platform/branding/favicon", content=too_big)).status_code == 413

    async def test_delete_restores_default(self, public_client):
        await public_client.put("/platform/branding/logo", content=PNG)
        deleted = await public_client.delete("/platform/branding/logo")
        assert deleted.status_code == 200
        assert "logo_version" not in deleted.json()
        assert (await public_client.get("/public/branding/logo")).status_code == 404

    async def test_unknown_asset_kind(self, public_client):
        assert (await public_client.put("/platform/branding/banner", content=PNG)).status_code == 422


class TestPermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "branding-viewer-oid",
            "sub": "branding-viewer-oid",
            "preferred_username": "viewer@test.com",
            "name": "Viewer",
            "roles": [],
        }

    async def test_writes_require_platform_config_write(self, public_client):
        assert (await public_client.put("/platform/branding", json={"theme_id": "slate"})).status_code == 403
        assert (await public_client.put("/platform/branding/logo", content=PNG)).status_code == 403
        assert (await public_client.delete("/platform/branding/logo")).status_code == 403
        assert (await public_client.get("/public/branding")).status_code == 200
