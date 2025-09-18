"""add timestamp column to wallet_transactions
Revision ID: 20240918_add_timestamp_wallet_transactions
Revises: <PUT_PREVIOUS_REVISION_ID_HERE>
Create Date: 2025-09-18
"""
from alembic import op
import sqlalchemy as sa
# --- Revision identifiers, used by Alembic ---
revision = "20240918_add_timestamp_wallet_transactions"
down_revision = "<PUT_PREVIOUS_REVISION_ID_HERE>"  # :warning: replace with your last migration ID
branch_labels = None
depends_on = None
def upgrade() -> None:
    # Add column with default NOW()
    op.add_column(
        "wallet_transactions",
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
def downgrade() -> None:
    op.drop_column("wallet_transactions", "timestamp")
