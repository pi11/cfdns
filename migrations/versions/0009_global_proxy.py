"""Add an optional global API proxy."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("encrypted_global_proxy", sa.Text()))


def downgrade() -> None:
    op.drop_column("app_settings", "encrypted_global_proxy")
