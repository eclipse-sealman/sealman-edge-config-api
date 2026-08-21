from typing import Dict, List
from abc import ABC, abstractmethod


class DeviceTemplateRepository(ABC):
    @abstractmethod
    async def get_template_variables(self) -> Dict[str, str]:
        """Returns all device template variables, name -> value."""
        ...

    @abstractmethod
    async def set_template_variable(self, name: str, value: str) -> Dict[str, str]:
        """Upserts a single variable and returns all variables afterwards."""
        ...

    @abstractmethod
    async def delete_template_variable(self, name: str) -> None:
        """Removes a variable. No-op if it doesn't exist."""
        ...

    @abstractmethod
    async def get_selected_templates(self) -> List[str]:
        """Returns the names of the templates currently selected for new devices."""
        ...

    @abstractmethod
    async def save_selected_templates(self, templates: List[str]) -> List[str]:
        """Replaces the selected templates with the given list."""
        ...
