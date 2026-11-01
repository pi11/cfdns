"""Add independent origin GET monitoring for Cloudflare-proxied records."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dns_records",
        sa.Column(
            "http_check_direct_enabled", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.create_table(
        "http_direct_check_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "record_id",
            sa.Integer(),
            sa.ForeignKey("dns_records.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("url", sa.String(length=2400), nullable=False),
        sa.Column("final_url", sa.String(length=2400)),
        sa.Column("origin", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_http_direct_check_results_record_id", "http_direct_check_results", ["record_id"]
    )
    op.create_index("ix_http_direct_check_results_status", "http_direct_check_results", ["status"])
    op.create_table(
        "http_direct_notification_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "record_id",
            sa.Integer(),
            sa.ForeignKey("dns_records.id", ondelete="CASCADE"),
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
        "ix_http_direct_notification_states_record_id",
        "http_direct_notification_states",
        ["record_id"],
    )


def downgrade() -> None:
    op.drop_table("http_direct_notification_states")
    op.drop_table("http_direct_check_results")
    op.drop_column("dns_records", "http_check_direct_enabled")
