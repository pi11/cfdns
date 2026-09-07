"""Add HTTP GET monitoring and notification state."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dns_records",
        sa.Column("http_check_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "dns_records",
        sa.Column("http_check_scheme", sa.String(length=5), server_default="https", nullable=False),
    )
    op.add_column(
        "dns_records",
        sa.Column("http_check_path", sa.String(length=2048), server_default="/", nullable=False),
    )
    op.create_table(
        "http_check_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "record_id", sa.Integer(), sa.ForeignKey("dns_records.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column("url", sa.String(length=2300), nullable=False),
        sa.Column("final_url", sa.String(length=2300)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_http_check_results_record_id", "http_check_results", ["record_id"])
    op.create_index("ix_http_check_results_status", "http_check_results", ["status"])
    op.create_table(
        "http_notification_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "record_id", sa.Integer(), sa.ForeignKey("dns_records.id", ondelete="CASCADE"),
            nullable=False, unique=True,
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
        "ix_http_notification_states_record_id", "http_notification_states", ["record_id"]
    )


def downgrade() -> None:
    op.drop_table("http_notification_states")
    op.drop_table("http_check_results")
    op.drop_column("dns_records", "http_check_path")
    op.drop_column("dns_records", "http_check_scheme")
    op.drop_column("dns_records", "http_check_enabled")
