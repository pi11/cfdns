"""Add OVH scheduled cancellation state."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ovh_services",
        sa.Column(
            "cancellation_scheduled", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column(
        "ovh_services", sa.Column("cancellation_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("ovh_services", "cancellation_at")
    op.drop_column("ovh_services", "cancellation_scheduled")
