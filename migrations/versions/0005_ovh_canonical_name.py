"""Store the canonical OVH service name."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ovh_services", sa.Column("canonical_name", sa.String(253)))
    op.create_index("ix_ovh_services_canonical_name", "ovh_services", ["canonical_name"])


def downgrade() -> None:
    op.drop_index("ix_ovh_services_canonical_name", table_name="ovh_services")
    op.drop_column("ovh_services", "canonical_name")
