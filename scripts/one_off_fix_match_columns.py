"""Add timestamp column to wallet_transactions
Revision ID: add_timestamp_wallet_tx
Revises: <your_last_revision_id>
Create Date: 2025-09-18
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime
# revision identifiers, used by Alembic.
revision = "add_timestamp_wallet_tx"
down_revision = "<your_last_revision_id>"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.add_column(
        "wallet_transactions",
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
def downgrade() -> None:
    op.drop_column("wallet_transactions", "timestamp")
