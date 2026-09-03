"""Add read-only ATW accounts and service cache."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "atw_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("username", sa.String(253), nullable=False),
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
        "atw_services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("atw_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("atw_id", sa.String(160), nullable=False),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("customer_name", sa.String(253)),
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
        sa.UniqueConstraint("account_id", "atw_id"),
    )
    op.create_index("ix_atw_services_name", "atw_services", ["name"])
    op.create_index("ix_atw_services_ips", "atw_services", ["ips"])


def downgrade() -> None:
    op.drop_table("atw_services")
    op.drop_table("atw_accounts")
