"""Widen the HTTP check URL columns to fit the longest URL the app can build."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("http_check_results") as batch:
        batch.alter_column("url", type_=sa.String(length=2400), existing_nullable=False)
        batch.alter_column("final_url", type_=sa.String(length=2400), existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("http_check_results") as batch:
        batch.alter_column("url", type_=sa.String(length=2300), existing_nullable=False)
        batch.alter_column("final_url", type_=sa.String(length=2300), existing_nullable=True)
