from typing import Dict, List

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.device_template import DeviceTemplateVariable, SelectedDeviceTemplate
from db.registry import register_repository
from db.repos.device_template import DeviceTemplateRepository


@register_repository(DeviceTemplateRepository)
class SqlAlchemyDeviceTemplateRepository(DeviceTemplateRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_template_variables(self) -> Dict[str, str]:
        result = await self._session.execute(select(DeviceTemplateVariable))
        return {row.variable_name: row.variable_value for row in result.scalars().all()}

    async def set_template_variable(self, name: str, value: str) -> Dict[str, str]:
        stmt = pg_insert(DeviceTemplateVariable).values(
            variable_name=name, variable_value=value
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DeviceTemplateVariable.variable_name],
            set_={"variable_value": stmt.excluded.variable_value},
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return await self.get_template_variables()

    async def delete_template_variable(self, name: str) -> None:
        await self._session.execute(
            delete(DeviceTemplateVariable).where(DeviceTemplateVariable.variable_name == name)
        )
        await self._session.commit()

    async def get_selected_templates(self) -> List[str]:
        result = await self._session.execute(select(SelectedDeviceTemplate))
        return [row.template_name for row in result.scalars().all()]

    async def save_selected_templates(self, templates: List[str]) -> List[str]:
        await self._session.execute(delete(SelectedDeviceTemplate))
        if templates:
            await self._session.execute(
                pg_insert(SelectedDeviceTemplate).values(
                    [{"template_name": name} for name in templates]
                )
            )
        await self._session.commit()
        return templates
