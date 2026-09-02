"""alter pipelines run_id to bigint

Revision ID: 0004_alter_run_id_bigint
Revises: 0003_create_pipelines
Create Date: 2026-09-01 21:58:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_alter_run_id_bigint"
down_revision: str | None = "0003_create_pipelines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "pipelines",
        "run_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "pipelines",
        "run_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
