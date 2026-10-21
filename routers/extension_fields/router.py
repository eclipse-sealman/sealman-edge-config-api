from typing import List, Never

from fastapi import APIRouter, Depends, HTTPException

from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Platform
from db.repos.endpoint import EndpointRepository
from db.repos.extension import ExtensionRepository
from db.session import get_repository
from exceptions import APIError
from extensions.endpoint_fields import apply_extension_fields, list_extension_field_sets
from routers.extension_fields.schemas import (
    ApplyExtensionFieldsRequest,
    ApplyExtensionFieldsResponse,
    ExtensionFieldSet,
)

# Always registered, also with EXTENSIONS_ENABLED=false: the declared fields are stored data.
extension_fields = APIRouter()

_read = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None))
_write = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None))


def _handle_api_error(exc: APIError) -> Never:
    raise HTTPException(status_code=exc.status_code, detail=str(exc))


@extension_fields.get(
    "/extension-fields",
    response_model=List[ExtensionFieldSet],
    tags=["Extension Fields"],
    summary="List declared extension fields and where they are applied",
)
async def list_extension_fields(
    extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    _auth=_read,
):
    """Lists the extensions that declare endpoint fields, and the types each field is applied to."""
    return await list_extension_field_sets(extension_repo, endpoint_repo)


@extension_fields.post(
    "/extension-fields/{extension}/apply",
    response_model=ApplyExtensionFieldsResponse,
    tags=["Extension Fields"],
    summary="Apply an extension's fields to endpoint types",
)
async def apply_fields(
    extension: str,
    body: ApplyExtensionFieldsRequest,
    extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    _auth=_write,
):
    """Copies the chosen declared fields onto the chosen types and marks them managed.

    A key a type already has is skipped (`key_exists`) and left unchanged.
    """
    try:
        return await apply_extension_fields(
            extension_repo, endpoint_repo, extension, body.type_ids, body.keys
        )
    except APIError as exc:
        _handle_api_error(exc)
