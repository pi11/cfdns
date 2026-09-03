"""Add application settings and SSL notification state."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("encrypted_telegram_token", sa.Text()),
        sa.Column("telegram_bot_username", sa.String(100)),
        sa.Column("telegram_chat_id", sa.String(32)),
        sa.Column(
            "hide_included_services", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "ssl_notification_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "record_id",
            sa.Integer(),
            sa.ForeignKey("dns_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("state_key", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("record_id", "ip_address"),
    )
    op.create_index(
        "ix_ssl_notification_states_record_id", "ssl_notification_states", ["record_id"]
    )


def downgrade() -> None:
    op.drop_table("ssl_notification_states")
    op.drop_table("app_settings")
