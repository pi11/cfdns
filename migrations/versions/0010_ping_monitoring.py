"""Add per-IP ping monitoring and notification state."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dns_records",
        sa.Column("ping_check_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "ping_check_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "record_id",
            sa.Integer(),
            sa.ForeignKey("dns_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("record_id", "ip_address"),
    )
    op.create_index("ix_ping_check_results_record_id", "ping_check_results", ["record_id"])
    op.create_index("ix_ping_check_results_status", "ping_check_results", ["status"])
    op.create_table(
        "ping_notification_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "record_id",
            sa.Integer(),
            sa.ForeignKey("dns_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("state_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("record_id", "ip_address"),
    )
    op.create_index(
        "ix_ping_notification_states_record_id", "ping_notification_states", ["record_id"]
    )


def downgrade() -> None:
    op.drop_table("ping_notification_states")
    op.drop_table("ping_check_results")
    op.drop_column("dns_records", "ping_check_enabled")
