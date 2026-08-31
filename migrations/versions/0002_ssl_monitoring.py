"""Add per-IP SSL monitoring."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dns_records",
        sa.Column("ssl_check_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "ssl_check_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "record_id",
            sa.Integer(),
            sa.ForeignKey("dns_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("certificate_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("record_id", "ip_address"),
    )
    op.create_index("ix_ssl_check_results_record_id", "ssl_check_results", ["record_id"])
    op.create_index("ix_ssl_check_results_status", "ssl_check_results", ["status"])


def downgrade() -> None:
    op.drop_table("ssl_check_results")
    op.drop_column("dns_records", "ssl_check_enabled")
