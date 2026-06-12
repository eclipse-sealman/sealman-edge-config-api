"""seed_actions

Revision ID: 8d4d5d5c121e
Revises: 0716880095d7
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d4d5d5c121e'
down_revision: Union[str, None] = '0716880095d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACTION_ROWS = [
    {
        "name": "device.read",
        "description": "Read device details",
        "is_global": False,
    },
    {
        "name": "device.deployment.write",
        "description": "Create or update device deployments",
        "is_global": False,
    },
    {
        "name": "device.module.execute_method",
        "description": "Execute direct methods on a device module",
        "is_global": False,
    },
    {
        "name": "device.network.write",
        "description": "Update device network settings",
        "is_global": False,
    },
    {
        "name": "device.sems_template.apply",
        "description": "Apply the default Smart EMS template to a device",
        "is_global": False,
    },
    {
        "name": "device.password.read",
        "description": "Read a device password",
        "is_global": False,
    },
    {
        "name": "device.password.write",
        "description": "Update a device password",
        "is_global": False,
    },
    {
        "name": "device.module_twin_config.write",
        "description": "Update device module twin configuration",
        "is_global": False,
    },
    {
        "name": "device.network.discover",
        "description": "Run device network discovery",
        "is_global": False,
    },
    {
        "name": "device.line.write",
        "description": "Update device line settings",
        "is_global": False,
    },
    {
        "name": "platform.authorization.read",
        "description": "Read platform authorization data (users, teams, roles, permissions)",
        "is_global": True,
    },
    {
        "name": "platform.authorization.write",
        "description": "Write platform authorization data (users, teams, roles, permissions)",
        "is_global": True,
    },
]


def upgrade() -> None:
    actions_table = sa.table(
        "actions",
        sa.column("name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("is_global", sa.Boolean()),
    )

    op.bulk_insert(actions_table, ACTION_ROWS)


def downgrade() -> None:
    action_names = [row["name"] for row in ACTION_ROWS]
    actions_table = sa.table("actions", sa.column("name", sa.Text()))

    op.execute(actions_table.delete().where(actions_table.c.name.in_(action_names)))
