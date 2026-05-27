"""Add UserContext and authorization models

Revision ID: 745cd0070e97
Revises: c3fd04c560ae
Create Date: 2026-05-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '745cd0070e97'
down_revision: Union[str, None] = 'c3fd04c560ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('actions',
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_global', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('roles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('scopes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('attr', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('access_rule', sa.Enum('ALL', 'ANY', name='accessrule'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('user_context',
    sa.Column('user_id', sa.Text(), nullable=False),
    sa.Column('preferred_username', sa.Text(), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('is_new_user', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('role_actions',
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('action_name', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['action_name'], ['actions.name'], ),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ),
    sa.PrimaryKeyConstraint('role_id', 'action_name')
    )
    op.create_table('teams',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('scope_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['scope_id'], ['scopes.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('team_assigned_roles',
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('team_id', 'role_id')
    )
    op.create_table('user_context_teams',
    sa.Column('user_id', sa.Text(), nullable=False),
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user_context.user_id'], ),
    sa.PrimaryKeyConstraint('user_id', 'team_id')
    )


def downgrade() -> None:
    op.drop_table('user_context_teams')
    op.drop_table('team_assigned_roles')
    op.drop_table('teams')
    op.drop_table('role_actions')
    op.drop_table('user_context')
    op.drop_table('scopes')
    op.drop_table('roles')
    op.drop_table('actions')
