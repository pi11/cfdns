"""Add optional Telegram proxy settings."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("encrypted_telegram_proxy", sa.Text()))


def downgrade() -> None:
    op.drop_column("app_settings", "encrypted_telegram_proxy")
