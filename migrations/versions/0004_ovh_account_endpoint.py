"""Add the OVH API region to accounts."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ovh_accounts",
        sa.Column("endpoint", sa.String(16), server_default="ovh-eu", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ovh_accounts", "endpoint")
