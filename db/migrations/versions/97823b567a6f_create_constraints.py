"""Create constraints

Revision ID: 97823b567a6f
Revises: b4df9f3527d5
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '97823b567a6f'
down_revision: Union[str, None] = 'b4df9f3527d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('active_deployment', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True,
               existing_server_default=sa.text('now()'))
    op.drop_constraint(op.f('password_renewal_tasks_device_id_fkey'), 'password_renewal_tasks', type_='foreignkey')
    op.create_foreign_key(None, 'password_renewal_tasks', 'devices', ['device_id'], ['device_id'])


def downgrade() -> None:
    op.drop_constraint(op.f('password_renewal_tasks_device_id_fkey'), 'password_renewal_tasks', type_='foreignkey')
    op.create_foreign_key(op.f('password_renewal_tasks_device_id_fkey'), 'password_renewal_tasks', 'devices', ['device_id'], ['device_id'], ondelete='CASCADE')
    op.alter_column('active_deployment', 'updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False,
               existing_server_default=sa.text('now()'))