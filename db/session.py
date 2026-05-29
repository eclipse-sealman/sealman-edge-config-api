from typing import Callable, Type, TypeVar, cast

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from constants import POSTGRES_URL
from db.models import auto_import_models
from db.sqlalchemy import auto_import_repositories
from helper import normalize_database_url
from db.registry import repo_registry

auto_import_models()
auto_import_repositories()

DATABASE_URL = normalize_database_url(POSTGRES_URL)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
        
T = TypeVar("T")

def get_repository(interface: Type[T]) -> Callable[..., T]:
    def _get_repo(session: AsyncSession = Depends(get_db)) -> T:
        repo_cls = repo_registry.get(interface)
        repo_factory = cast(Callable[[AsyncSession], T], repo_cls)
        return repo_factory(session)
    return _get_repo