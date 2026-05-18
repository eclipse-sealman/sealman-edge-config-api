import yaml

from typing import Optional, Dict, List
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import (
    select,
    update,
    delete,
    func
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from db.models.compose import ActiveDeployment, ComposeDeployment
from db.registry import register_repository
from db.repos.compose import ComposeRepository

@register_repository(ComposeRepository)
class SQLAlchemyComposeRepository(ComposeRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _get_all_exposed_ports_from_db(
        self,
        exclude_name: Optional[str] = None
    ) -> tuple:
        query = select(
            ComposeDeployment.name,
            ComposeDeployment.exposed_ports
        )
        if exclude_name:
            query = query.filter(ComposeDeployment.name != exclude_name)

        result = await self._session.execute(query)
        ports: set[int] = set()
        names: List[str] = []
        for row in result.all():
            if row.exposed_ports:
                ports.update(row.exposed_ports)
                names.append(row.name)
        return ports, names

    # ---------------------------
    # CRUD
    # ---------------------------

    async def create_or_update(
        self,
        name: str,
        request: Dict,
        content: Dict,
        landing_page: Optional[bool] = False,
        description: Optional[str] = None,
    ) -> bool:

        try:
            existing_stmt = select(ComposeDeployment).where(
                ComposeDeployment.name == name
            )
            existing_result = await self._session.execute(existing_stmt)
            existing = existing_result.scalar_one_or_none()

            exclude_name = name if existing else None

            data = await self._prepare_compose_data(
                name,
                request,
                content,
                exclude_name=exclude_name
            )

            if existing:
                stmt = (
                    update(ComposeDeployment)
                    .where(ComposeDeployment.name == name)
                    .values(
                        request=data["request"],
                        content=data["content"],
                        sems_compose=data["sems_compose"],
                        exposed_ports=data["exposed_ports"],
                        description=description,
                        landing_page=landing_page,
                        updated_at=func.now(),
                    )
                )
                
                await self._session.execute(stmt)
            else:
                stmt = None
                self._session.add(
                    ComposeDeployment(
                        name=name,
                        request=data["request"],
                        content=data["content"],
                        sems_compose=data["sems_compose"],
                        exposed_ports=data["exposed_ports"],
                        description=description,
                        landing_page=landing_page,
                    )
                )

            await self._session.flush()
            await self._session.commit()

            return True

        except HTTPException:
            await self._session.rollback()
            raise

        except IntegrityError:
            await self._session.rollback()
            return False

    async def get(self, name: str, landing_page: Optional[bool] = None) -> Optional[Dict]:

        query = select(
            ComposeDeployment.name,
            ComposeDeployment.description,
            ComposeDeployment.request,
            ComposeDeployment.content,
            ComposeDeployment.sems_compose,
        ).where(ComposeDeployment.name == name)

        if landing_page is not None:
            query = query.filter(ComposeDeployment.landing_page == landing_page)

        result = await self._session.execute(query)
        row = result.mappings().first()

        if not row:
            return None

        return {
            "name": row["name"],
            "description": row["description"],
            "request": row["request"],
            "compose": row["content"],
            "sems_compose": row["sems_compose"],
        }

    async def delete(
        self,
        name: str,
        landing_page: Optional[bool] = None
    ) -> bool:
        query = delete(ComposeDeployment).where(ComposeDeployment.name == name)
        if landing_page:
            query = query.filter(ComposeDeployment.landing_page == landing_page)
        result = await self._session.execute(query)
        await self._session.commit()
        return result.rowcount > 0

    async def list_names(self, prefix: Optional[str] = None) -> List[str]:
        query = select(ComposeDeployment.name)
        if prefix:
            query = query.filter(ComposeDeployment.name.like(f"{prefix}%"))
        result = await self._session.execute(query)
        return [row.name for row in result.all()]

    async def list(
        self,
        prefix: Optional[str] = None,
        landing_page: Optional[bool] = None
    ) -> List[Dict]:
        query = select(
            ComposeDeployment.name,
            ComposeDeployment.description,
            ComposeDeployment.landing_page,
            ComposeDeployment.created_at,
            ComposeDeployment.updated_at
        )
        if prefix:
            query = query.filter(ComposeDeployment.name.like(f"{prefix}%"))
        if landing_page:
            query = query.filter(ComposeDeployment.landing_page == landing_page)

        result = await self._session.execute(query)
        return [
            {
                "name": row.name,
                "description": row.description,
                "landing_page": row.landing_page,
                "created_at": row.created_at,
                "updated_at": row.updated_at
            }
            for row in result.all()
        ]
        
    async def set_active_deployment(self, name: str) -> None:
        stmt = insert(ActiveDeployment).values(
            id="active",
            deployment_name=name,
        )

        stmt = stmt.on_conflict_do_update(
            index_elements=[ActiveDeployment.id],
            set_={
                "deployment_name": name,
                "updated_at": func.now(),
            },
        )

        await self._session.execute(stmt)
        await self._session.commit()
        
    async def get_active_deployment(self) -> Optional[str]:
        stmt = select(ActiveDeployment.deployment_name).where(
            ActiveDeployment.id == "active"
        )

        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        
        return row
    
    async def delete_active_deployment(self) -> bool:
        stmt = delete(ActiveDeployment).where(ActiveDeployment.id == "active")
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0
            
    def _get_exposed_ports_from_request(self, request: Dict) -> List[int]:
        exposed_ports: List[int] = []

        for service in request.get("services", []):
            for port_exposing in service.get("exposed_ports", []):
                exposed_port = int(port_exposing.split(":")[0])

                if exposed_port in exposed_ports:
                    raise ValueError(
                        f"Service <{service.get('name')}> tries to expose a port "
                        f"<{exposed_port}> which is already occupied"
                    )

                exposed_ports.append(exposed_port)

        return exposed_ports

    async def _handle_exposed_ports_on_request(
        self,
        request: Dict,
        *,
        exclude_name: Optional[str] = None
    ) -> List[int]:
        new_ports = set(self._get_exposed_ports_from_request(request))
        existing_ports, names = await self._get_all_exposed_ports_from_db(exclude_name)

        conflicts = new_ports & existing_ports
        if conflicts:
            raise HTTPException(
                status_code=422,
                detail=f"Requested ports {new_ports} conflicting on ports {conflicts} with existing "
                       f"port exposing introduced by deployments: {names}"
            )

        return list(new_ports)

    def _gen_sems_compose(self, name: str, compose: Dict) -> Dict[str, str]:
        return {
            name: yaml.dump(compose, sort_keys=False)
        }

    async def _prepare_compose_data(
        self,
        name: str,
        request: Dict,
        content: Dict,
        *,
        exclude_name: Optional[str] = None
    ) -> Dict:
        return {
            "request": request,
            "content": content,
            "sems_compose": self._gen_sems_compose(name, content),
            "exposed_ports": await self._handle_exposed_ports_on_request(
                request,
                exclude_name=exclude_name
            )
        }