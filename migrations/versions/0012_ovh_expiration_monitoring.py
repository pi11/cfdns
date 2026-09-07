"""Add OVH service expiration monitoring."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ovh_services", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("ovh_services", sa.Column("auto_renew", sa.Boolean()))
    op.create_index("ix_ovh_services_expires_at", "ovh_services", ["expires_at"])
    op.create_table(
        "ovh_expiration_notification_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("ovh_services.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("state_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_ovh_expiration_notification_states_service_id",
        "ovh_expiration_notification_states",
        ["service_id"],
    )


def downgrade() -> None:
    op.drop_table("ovh_expiration_notification_states")
    op.drop_index("ix_ovh_services_expires_at", table_name="ovh_services")
    op.drop_column("ovh_services", "auto_renew")
    op.drop_column("ovh_services", "expires_at")
