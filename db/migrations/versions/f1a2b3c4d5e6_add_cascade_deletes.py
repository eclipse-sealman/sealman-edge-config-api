"""Add cascade deletes to role_actions and team_assigned_roles

Revision ID: f1a2b3c4d5e6
Revises: 97823b567a6f
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '97823b567a6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # role_actions: cascade when a role is deleted
    op.drop_constraint('role_actions_role_id_fkey', 'role_actions', type_='foreignkey')
    op.create_foreign_key(
        'role_actions_role_id_fkey', 'role_actions', 'roles',
        ['role_id'], ['id'], ondelete='CASCADE',
    )

    # team_assigned_roles: cascade when a role or team is deleted
    op.drop_constraint('team_assigned_roles_role_id_fkey', 'team_assigned_roles', type_='foreignkey')
    op.create_foreign_key(
        'team_assigned_roles_role_id_fkey', 'team_assigned_roles', 'roles',
        ['role_id'], ['id'], ondelete='CASCADE',
    )

    op.drop_constraint('team_assigned_roles_team_id_fkey', 'team_assigned_roles', type_='foreignkey')
    op.create_foreign_key(
        'team_assigned_roles_team_id_fkey', 'team_assigned_roles', 'teams',
        ['team_id'], ['id'], ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint('team_assigned_roles_team_id_fkey', 'team_assigned_roles', type_='foreignkey')
    op.create_foreign_key(
        'team_assigned_roles_team_id_fkey', 'team_assigned_roles', 'teams',
        ['team_id'], ['id'],
    )

    op.drop_constraint('team_assigned_roles_role_id_fkey', 'team_assigned_roles', type_='foreignkey')
    op.create_foreign_key(
        'team_assigned_roles_role_id_fkey', 'team_assigned_roles', 'roles',
        ['role_id'], ['id'],
    )

    op.drop_constraint('role_actions_role_id_fkey', 'role_actions', type_='foreignkey')
    op.create_foreign_key(
        'role_actions_role_id_fkey', 'role_actions', 'roles',
        ['role_id'], ['id'],
    )

