"""Add read-only OVH accounts and service cache."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ovh_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "ovh_services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("ovh_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ovh_id", sa.String(160), nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("service_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(64)),
        sa.Column("region", sa.String(100)),
        sa.Column("ips", sa.Text()),
        sa.Column("price", sa.String(100)),
        sa.Column("raw_json", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", "ovh_id"),
    )
    op.create_index("ix_ovh_services_name", "ovh_services", ["name"])
    op.create_index("ix_ovh_services_ips", "ovh_services", ["ips"])


def downgrade() -> None:
    op.drop_table("ovh_services")
    op.drop_table("ovh_accounts")
