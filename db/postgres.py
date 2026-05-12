from fastapi import Depends
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from constants import POSTGRES_URL, SQLITE_URL
from db.repos.device import DeviceRepository
from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from db.sqlalchemy.device import SqlAlchemyDeviceRepository
from db.sqlalchemy.password_renewal_task import SqlAlchemyPasswordRenewalTaskRepository
from exceptions import APIError

if POSTGRES_URL and POSTGRES_URL.strip():
    DeviceRepoImpl = SqlAlchemyDeviceRepository
    PasswordRenewalTaskRepoImpl = SqlAlchemyPasswordRenewalTaskRepository

elif SQLITE_URL and SQLITE_URL.strip():
    pass
    raise APIError("SQLITE not implemented yet.", 500)

else:
    raise APIError("No database configuration found.", 500)

engine = create_async_engine(POSTGRES_URL, pool_pre_ping=True)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

def create_device_repository(session: AsyncSession) -> DeviceRepository:
    return DeviceRepoImpl(session)

def create_password_renewal_task_repository(session: AsyncSession) -> PasswordRenewalTaskRepository:
    return PasswordRenewalTaskRepoImpl(session)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
        
async def get_device_repository(
    session: AsyncSession = Depends(get_db),
) -> DeviceRepository:
    return create_device_repository(session)

async def get_password_renewal_task_repository(
    session: AsyncSession = Depends(get_db),
) -> PasswordRenewalTaskRepository:
    return create_password_renewal_task_repository(session)