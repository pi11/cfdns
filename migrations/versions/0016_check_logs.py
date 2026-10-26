"""Keep a history of every SSL, ping, and GET check result."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "check_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("check_type", sa.String(length=8), nullable=False),
        sa.Column(
            "record_id",
            sa.Integer(),
            sa.ForeignKey("dns_records.id", ondelete="SET NULL"),
        ),
        sa.Column("record_name", sa.String(length=253), nullable=False),
        sa.Column("target", sa.String(length=2400), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("certificate_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_check_logs_check_type", "check_logs", ["check_type"])
    op.create_index("ix_check_logs_status", "check_logs", ["status"])
    op.create_index("ix_check_logs_success", "check_logs", ["success"])
    op.create_index("ix_check_logs_checked_at", "check_logs", ["checked_at"])
    op.create_index("ix_check_logs_record_checked", "check_logs", ["record_id", "checked_at"])


def downgrade() -> None:
    op.drop_table("check_logs")
