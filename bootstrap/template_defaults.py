import logging
import re
from functools import lru_cache
from pathlib import Path

from db.registry import repo_registry
from db.repos.device_template import DeviceTemplateRepository

logger = logging.getLogger("EdgeConfigAPI")

_CONFIG_FILE = Path(__file__).parent / "sealman-sems-config.twig"
_DEFAULT_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\|\s*default\('([^']*)'\)\s*\}\}")


@lru_cache(maxsize=1)
def read_template_defaults() -> dict[str, str]:
    """All variables the Twig template renders with a quoted default (an empty default is an empty string)."""
    return dict(_DEFAULT_PATTERN.findall(_CONFIG_FILE.read_text(encoding="utf-8")))


def read_sems_connection_defaults() -> dict[str, str]:
    """`sems_*` variables the Twig template declares with a default."""
    return {name: value for name, value in read_template_defaults().items() if name.startswith("sems_")}


async def seed_sems_connection_defaults() -> None:
    """Store the Twig defaults for SEMS connection variables that are not yet set in the DB."""
    from db.session import AsyncSessionLocal

    defaults = read_sems_connection_defaults()
    async with AsyncSessionLocal() as session:
        repo = repo_registry.get(DeviceTemplateRepository)(session)
        existing = await repo.get_template_variables()
        for name, value in defaults.items():
            if name not in existing:
                await repo.set_template_variable(name, value)
                logger.info(f"Seeded SEMS template variable '{name}' with the template default")
