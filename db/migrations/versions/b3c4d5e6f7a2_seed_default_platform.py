"""seed default platform row

Revision ID: b3c4d5e6f7a2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platform (name, platform_meta)
        VALUES (
            'default',
            '{"description": null, "countryCode": null,
              "city": null, "geoLocation": null}'::jsonb
        )
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM platform WHERE name = 'default'")

