"""Initial schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
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
        "zones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cloudflare_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", "cloudflare_id"),
    )
    op.create_index("ix_zones_name", "zones", ["name"])
    op.create_table(
        "dns_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "zone_id", sa.Integer(), sa.ForeignKey("zones.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("cloudflare_id", sa.String(64), nullable=False),
        sa.Column("record_type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ttl", sa.Integer(), nullable=False),
        sa.Column("proxied", sa.Boolean()),
        sa.Column("proxiable", sa.Boolean(), nullable=False),
        sa.Column("cloudflare_comment", sa.Text()),
        sa.Column("local_comment", sa.Text()),
        sa.Column("data_json", sa.Text()),
        sa.Column("priority", sa.Integer()),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("zone_id", "cloudflare_id"),
    )
    op.create_index("ix_dns_records_name", "dns_records", ["name"])
    op.create_index("ix_dns_records_content", "dns_records", ["content"])
    op.create_index("ix_dns_records_record_type", "dns_records", ["record_type"])
    op.create_index(
        "ix_dns_records_search", "dns_records", ["name", "content", "cloudflare_comment"]
    )


def downgrade() -> None:
    op.drop_table("dns_records")
    op.drop_table("zones")
    op.drop_table("accounts")
