from uuid import UUID

from sqlalchemy.exc import IntegrityError

from db.repos.scope import ScopeRepository
from exceptions import APIError
from routers.auth.schemas import ScopeCreateRequest, ScopeUpdateRequest


async def get_scopes(scope_repo: ScopeRepository):
    return await scope_repo.list()


async def create_scope(
    request: ScopeCreateRequest,
    scope_repo: ScopeRepository,
):
    try:
        return await scope_repo.create(
            name=request.name,
            description=request.description,
            attr=request.attr,
            access_rule=request.access_rule,
        )
    except IntegrityError:
        raise APIError(f"Scope with name '{request.name}' already exists", 409)


async def update_scope(
    scope_id: UUID,
    request: ScopeUpdateRequest,
    scope_repo: ScopeRepository,
):
    if await scope_repo.get(scope_id) is None:
        raise APIError(f"Scope '{scope_id}' was not found", 404)

    try:
        scope = await scope_repo.update(
            scope_id=scope_id,
            name=request.name,
            description=request.description,
            attr=request.attr,
            access_rule=request.access_rule,
        )
    except IntegrityError:
        raise APIError(f"Scope with name '{request.name}' already exists", 409)

    if scope is None:
        raise APIError(f"Scope '{scope_id}' was not found", 404)

    return scope


async def delete_scope(scope_id: UUID, scope_repo: ScopeRepository):
    if await scope_repo.get(scope_id) is None:
        raise APIError(f"Scope '{scope_id}' was not found", 404)
    await scope_repo.delete(scope_id)

